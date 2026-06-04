"""Downsampling, batching buffer for forward tick collection.

market-data publishes every WebSocket tick (thousands/sec). Persisting each to
BigQuery would be ruinously expensive and isn't needed: the opportunity engine
evaluates at ~1s, so 1 tick/(exchange,symbol)/second is plenty for a
cross-exchange backtest. This buffer:

  * downsamples to one row per (exchange, symbol) per ``downsample_s``,
  * batches rows and flushes by size or age (one BigQuery streaming insert
    per batch instead of per tick).

The clock is injected (``now``) so this is unit-testable without real time or
BigQuery. Best-effort by design — ticks are not audit-critical.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from writer import tick_to_row

logger = logging.getLogger("trade-ledger.tick_collector")


class TickBuffer:
    def __init__(
        self,
        flush_fn: Callable[[list[dict]], None],
        max_rows: int = 500,
        downsample_s: float = 1.0,
        flush_interval_s: float = 5.0,
    ) -> None:
        self._flush_fn = flush_fn
        self._max_rows = max_rows
        self._downsample_s = downsample_s
        self._flush_interval_s = flush_interval_s
        self._rows: list[dict] = []
        self._last_seen: dict[tuple[str, str], float] = {}
        self._last_flush: float = 0.0
        self.dropped = 0      # downsampled-away
        self.written = 0      # successfully handed to flush_fn

    def add(self, tick, now: float) -> None:
        key = (tick.exchange, tick.symbol)
        last = self._last_seen.get(key)
        if last is not None and (now - last) < self._downsample_s:
            self.dropped += 1
            return
        self._last_seen[key] = now
        self._rows.append(tick_to_row(tick))
        if len(self._rows) >= self._max_rows or (now - self._last_flush) >= self._flush_interval_s:
            self.flush(now)

    def flush(self, now: float = 0.0) -> None:
        self._last_flush = now
        if not self._rows:
            return
        batch, self._rows = self._rows, []
        try:
            self._flush_fn(batch)
            self.written += len(batch)
        except Exception:  # noqa: BLE001 — best-effort; never let tick flushing crash the consumer
            logger.exception("tick batch flush failed (%d rows dropped)", len(batch))
