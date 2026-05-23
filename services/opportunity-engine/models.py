"""Internal models for the opportunity-engine.

The engine maintains an in-memory snapshot of the latest tick per
(exchange, symbol) and the latest funding rate per (exchange, asset). New
Pub/Sub messages mutate the snapshot, then trigger strategy evaluation.

For funding-rate trend detection we also keep a small ring buffer of the
last ``_FUNDING_HISTORY_DEPTH`` observations per (exchange, asset) and
expose ``ema()`` / ``is_trending_positive()`` over it. The buffer lives
inside MarketSnapshot to stay consistent with the "in-memory snapshot"
architecture — we deliberately do NOT write to Redis from here (per the
CLAUDE.md key-namespace ownership rules).
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from shared.models.exchange_tick import ExchangeTick
from shared.models.funding_rate import FundingRate


_TICK_STALE_AFTER = timedelta(seconds=30)
_FUNDING_STALE_AFTER = timedelta(minutes=10)

# Trend-detection ring buffer depth. With CoinGlass polling at 60s, 48
# entries is ~48 minutes — enough to compute a 4h-equivalent EMA but small
# enough to fit comfortably in memory for thousands of (exchange, asset)
# pairs.
_FUNDING_HISTORY_DEPTH = 48

# EMA period (in observations). For the typical 1-minute funding poll this
# corresponds to a ~4-hour smoothing horizon when applied to 8h-period
# CEX funding (since funding values only change at 8h boundaries) and a
# more responsive smoothing on Hyperliquid's hourly funding.
_EMA_PERIOD = 4
_EMA_ALPHA = 2.0 / (_EMA_PERIOD + 1.0)


class MarketSnapshot:
    """Thread-safe snapshot of latest ticks, funding rates, and funding history."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ticks: dict[tuple[str, str], ExchangeTick] = {}
        self._funding: dict[tuple[str, str], FundingRate] = {}
        # Per (exchange, asset): deque of (observed_at, rate_per_period) tuples.
        self._funding_history: dict[
            tuple[str, str], deque[tuple[datetime, float]]
        ] = {}

    def update_tick(self, tick: ExchangeTick) -> None:
        with self._lock:
            self._ticks[(tick.exchange, tick.symbol)] = tick

    def update_funding(self, rate: FundingRate) -> None:
        with self._lock:
            self._funding[(rate.exchange, rate.asset)] = rate
            key = (rate.exchange, rate.asset)
            history = self._funding_history.setdefault(
                key, deque(maxlen=_FUNDING_HISTORY_DEPTH)
            )
            history.append((rate.observed_at, rate.rate_per_period))

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

    def funding_history(
        self, exchange: str, asset: str
    ) -> list[tuple[datetime, float]]:
        """Snapshot of the per-period funding history for one venue/asset."""
        with self._lock:
            buf = self._funding_history.get((exchange, asset))
            return list(buf) if buf is not None else []

    def funding_ema(self, exchange: str, asset: str) -> float | None:
        """EMA of the per-period funding rate. Returns None if too little data."""
        history = self.funding_history(exchange, asset)
        if len(history) < _EMA_PERIOD:
            return None
        ema = history[0][1]
        for _, rate in history[1:]:
            ema = rate * _EMA_ALPHA + ema * (1.0 - _EMA_ALPHA)
        return ema

    def is_funding_trending_positive(self, exchange: str, asset: str) -> bool:
        """True iff the latest rate is above the EMA AND positive.

        Returns False when we have too little data — fail-closed, never
        emit a candidate on a one-sample-old trend.
        """
        history = self.funding_history(exchange, asset)
        if len(history) < _EMA_PERIOD:
            return False
        ema = self.funding_ema(exchange, asset)
        if ema is None:
            return False
        latest = history[-1][1]
        return latest > ema and latest > 0


class EngineStatus(BaseModel):
    status: str
    ticks_tracked: int = 0
    funding_rates_tracked: int = 0
    opportunities_published_total: int = 0
    last_evaluation_at: datetime | None = None
    last_publish_at: datetime | None = None
    last_publish_threshold_bps: float | None = None
    config: dict[str, float] = Field(default_factory=dict)
