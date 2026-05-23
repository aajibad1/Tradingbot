"""ArbitrageScanner.io funding rate source.

Used as a cross-check against CoinGlass. If the two diverge by > 20%, we
publish only the CoinGlass reading and emit a warning to the audit log so
ops can investigate.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import httpx

from normalizer import from_arbitrage_scanner
from secret_loader import get_secret
from shared.models.funding_rate import FundingRate
from sources.base import FundingSource


_ARBSCAN_URL = "https://api.arbitragescanner.io/v1/funding"


class ArbitrageScannerSource(FundingSource):
    name = "arbitrage_scanner"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (
            api_key
            or os.environ.get("ARBITRAGE_SCANNER_API_KEY")
            or get_secret("arbitrage-scanner-api-key")
        )
        if not self.api_key:
            raise RuntimeError(
                "ARBITRAGE_SCANNER_API_KEY unset — set ARBITRAGE_SCANNER_API_KEY env var, "
                "LOCAL_SECRET_ARBITRAGE_SCANNER_API_KEY, or configure in Secret Manager"
            )

    async def fetch(self) -> Iterable[FundingRate]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            response = await client.get(_ARBSCAN_URL)
            response.raise_for_status()
            payload = response.json()

        rows = payload.get("data") or []
        normalized: list[FundingRate] = []
        for row in rows:
            rate = from_arbitrage_scanner(row)
            if rate is not None:
                normalized.append(rate)
        return normalized
