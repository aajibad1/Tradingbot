"""Internal models for the opportunity-engine.

The engine maintains an in-memory snapshot of the latest tick per
(exchange, symbol) and the latest funding rate per (exchange, asset). New
Pub/Sub messages mutate the snapshot, then trigger strategy evaluation.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from shared.models.exchange_tick import ExchangeTick
from shared.models.funding_rate import FundingRate


_TICK_STALE_AFTER = timedelta(seconds=30)
_FUNDING_STALE_AFTER = timedelta(minutes=10)


class MarketSnapshot:
    """Thread-safe snapshot of latest ticks and funding rates."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ticks: dict[tuple[str, str], ExchangeTick] = {}
        self._funding: dict[tuple[str, str], FundingRate] = {}

    def update_tick(self, tick: ExchangeTick) -> None:
        with self._lock:
            self._ticks[(tick.exchange, tick.symbol)] = tick

    def update_funding(self, rate: FundingRate) -> None:
        with self._lock:
            self._funding[(rate.exchange, rate.asset)] = rate

    def fresh_ticks_for_asset(self, asset: str, now: datetime | None = None) -> list[ExchangeTick]:
        now = now or datetime.utcnow()
        with self._lock:
            return [
                t
                for t in self._ticks.values()
                if t.asset == asset and (now - t.timestamp) < _TICK_STALE_AFTER
            ]

    def fresh_funding_for_asset(self, asset: str, now: datetime | None = None) -> list[FundingRate]:
        now = now or datetime.utcnow()
        with self._lock:
            return [
                f
                for f in self._funding.values()
                if f.asset == asset and (now - f.observed_at) < _FUNDING_STALE_AFTER
            ]

    def assets_with_ticks(self) -> set[str]:
        with self._lock:
            return {t.asset for t in self._ticks.values()}


class EngineStatus(BaseModel):
    status: str
    ticks_tracked: int = 0
    funding_rates_tracked: int = 0
    opportunities_published_total: int = 0
    last_evaluation_at: datetime | None = None
    last_publish_at: datetime | None = None
    config: dict[str, float] = Field(default_factory=dict)
