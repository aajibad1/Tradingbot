"""CoinGlass funding rate source.

Reference: https://docs.coinglass.com/reference/funding-rate
We use the v4 `/futures/funding_rate` endpoint which returns a snapshot
across the universe of supported exchanges in one call.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import httpx

from normalizer import from_coinglass
from secret_loader import get_secret
from shared.models.funding_rate import FundingRate
from sources.base import FundingSource


_COINGLASS_URL = "https://open-api-v4.coinglass.com/api/futures/funding_rate"


class CoinGlassSource(FundingSource):
    name = "coinglass"

    def __init__(self, api_key: str | None = None, exchanges: list[str] | None = None) -> None:
        self.api_key = api_key or os.environ.get("COINGLASS_API_KEY") or get_secret("coinglass-api-key")
        if not self.api_key:
            raise RuntimeError(
                "COINGLASS_API_KEY unset — set COINGLASS_API_KEY env var, "
                "LOCAL_SECRET_COINGLASS_API_KEY, or configure in Secret Manager"
            )
        self.exchanges = [e.lower() for e in (exchanges or [])]

    async def fetch(self) -> Iterable[FundingRate]:
        headers = {"CG-API-KEY": self.api_key}
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            response = await client.get(_COINGLASS_URL)
            response.raise_for_status()
            payload = response.json()

        rows = payload.get("data") or []
        normalized: list[FundingRate] = []
        for row in rows:
            if self.exchanges:
                if row.get("exchangeName", "").lower() not in self.exchanges:
                    continue
            rate = from_coinglass(row)
            if rate is not None:
                normalized.append(rate)
        return normalized
