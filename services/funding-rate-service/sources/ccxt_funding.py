"""CCXT-native funding rate polling.

Used for exchanges where the official funding endpoint is more reliable
than the third-party aggregators — Hyperliquid in particular publishes
1-hour funding that CoinGlass sometimes lags on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from normalizer import from_ccxt
from shared.models.funding_rate import FundingRate
from sources.base import FundingSource

logger = logging.getLogger(__name__)


class CCXTFundingSource(FundingSource):
    name = "ccxt"

    def __init__(self, exchanges: list[str]) -> None:
        if not exchanges:
            raise ValueError("CCXTFundingSource requires at least one exchange")
        self.exchanges = [e.lower() for e in exchanges]
        # CCXT clients are cached across poll cycles. Rebuilding one every 60s
        # re-loaded markets over HTTP and added rate-limit pressure; reusing the
        # client keeps the session warm. Closed once on shutdown via close().
        self._clients: dict[str, Any] = {}

    def _client_for(self, exchange: str) -> Any:
        client = self._clients.get(exchange)
        if client is None:
            import ccxt.async_support as ccxt

            client_cls = getattr(ccxt, _CCXT_ID_BY_EXCHANGE.get(exchange, exchange))
            client = client_cls({"enableRateLimit": True})
            self._clients[exchange] = client
        return client

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
        client = self._client_for(exchange)
        funding_rates = await client.fetch_funding_rates()

        normalized: list[FundingRate] = []
        for symbol, payload in funding_rates.items():
            rate = from_ccxt(exchange, {"symbol": symbol, **payload})
            if rate is not None:
                normalized.append(rate)
        return normalized

    async def close(self) -> None:
        """Close all cached CCXT clients — called once on service shutdown."""
        for exchange, client in self._clients.items():
            try:
                await client.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                logger.exception("error closing ccxt client for %s", exchange)
        self._clients.clear()


_CCXT_ID_BY_EXCHANGE = {
    "binance.us": "binanceus",
    "crypto.com": "cryptocom",
}


_CCXT_ID_BY_EXCHANGE = {
    "binance.us": "binanceus",
    "crypto.com": "cryptocom",
}
