"""CCXT-native funding rate polling.

Used for exchanges where the official funding endpoint is more reliable
than the third-party aggregators — Hyperliquid in particular publishes
1-hour funding that CoinGlass sometimes lags on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from normalizer import from_ccxt
from shared.models.funding_rate import FundingRate
from sources.base import FundingSource


class CCXTFundingSource(FundingSource):
    name = "ccxt"

    def __init__(self, exchanges: list[str]) -> None:
        if not exchanges:
            raise ValueError("CCXTFundingSource requires at least one exchange")
        self.exchanges = [e.lower() for e in exchanges]

    async def fetch(self) -> Iterable[FundingRate]:
        results = await asyncio.gather(
            *(self._fetch_one(e) for e in self.exchanges),
            return_exceptions=True,
        )
        rates: list[FundingRate] = []
        for r in results:
            if isinstance(r, Exception):
                # One exchange failing should not poison the whole cycle.
                continue
            rates.extend(r)
        return rates

    async def _fetch_one(self, exchange: str) -> list[FundingRate]:
        import ccxt.async_support as ccxt

        client_cls = getattr(ccxt, _CCXT_ID_BY_EXCHANGE.get(exchange, exchange))
        client = client_cls({"enableRateLimit": True})
        try:
            funding_rates = await client.fetch_funding_rates()
        finally:
            await client.close()

        normalized: list[FundingRate] = []
        for symbol, payload in funding_rates.items():
            rate = from_ccxt(exchange, {"symbol": symbol, **payload})
            if rate is not None:
                normalized.append(rate)
        return normalized


_CCXT_ID_BY_EXCHANGE = {
    "binance.us": "binanceus",
    "crypto.com": "cryptocom",
}
