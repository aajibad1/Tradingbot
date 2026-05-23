"""Funding source base class."""

from __future__ import annotations

from collections.abc import Iterable

from shared.models.funding_rate import FundingRate


class FundingSource:
    name: str

    async def fetch(self) -> Iterable[FundingRate]:
        """Return current funding rates from this source. Raises on transport errors."""
        raise NotImplementedError
