"""Base collector. Concrete subclasses wire in a CCXT Pro exchange client and
override `_symbols()` to pick which markets to watch.

The collector owns the connection: one persistent WebSocket per exchange,
reconnect with exponential backoff, normalize ticks, publish to Pub/Sub.

Latency observations are also pushed into Redis so the risk-engine can read
them via `check_exchange_health`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from shared.models.exchange_tick import ExchangeTick
from shared.pubsub.publisher import Topic, get_publisher
from shared.utils.exchange_normalizer import normalize_symbol

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


_RECONNECT_INITIAL_S = 1.0
_RECONNECT_MAX_S = 30.0
_HEALTH_KEY_PREFIX = "health:latency:"
# A single symbol may fail this many times (with backoff) before we give up on
# it and keep watching the rest — one bad symbol must not take down the venue.
_MAX_SYMBOL_FAILURES = 8
# Refresh the health key on a timer (< its 30s TTL) so a low-tick-rate venue
# isn't marked unhealthy just because no tick arrived recently.
_HEALTH_HEARTBEAT_S = 15.0


@dataclass
class CollectorConfig:
    exchange: str
    symbols: list[str]
    instrument_type: str = "spot"  # 'spot' or 'perp'


class BaseCollector:
    name: str

    def __init__(self, config: CollectorConfig, redis: Redis | None = None) -> None:
        self.config = config
        self.redis = redis
        self.publisher = get_publisher()
        self._stopping = asyncio.Event()
        self._last_message_at: float = 0.0
        self._last_latency_ms: int = 0
        self._client: Any | None = None

    def is_healthy(self) -> bool:
        # No message for 60s = stale connection.
        return (time.time() - self._last_message_at) < 60.0

    async def stop(self) -> None:
        self._stopping.set()
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.exception("error closing %s client", self.config.exchange)

    async def _build_client(self) -> Any:
        """Subclasses build the CCXT Pro client here (with creds from Secret Manager)."""
        raise NotImplementedError

    async def run(self) -> None:
        backoff = _RECONNECT_INITIAL_S
        while not self._stopping.is_set():
            try:
                self._client = await self._build_client()
                backoff = _RECONNECT_INITIAL_S  # reset on successful (re)connect
                logger.info("%s connected, watching %s", self.config.exchange, self.config.symbols)
                await self._watch_loop()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s collector crashed — backing off %.1fs", self.config.exchange, backoff)
                await asyncio.sleep(backoff)
                backoff = min(_RECONNECT_MAX_S, backoff * 2)
            finally:
                if self._client is not None:
                    try:
                        await self._client.close()
                    except Exception:
                        logger.exception("error closing %s client", self.config.exchange)
                    self._client = None

    async def _watch_loop(self) -> None:
        """CCXT Pro watchTicker per-symbol fan-out.

        Each symbol is watched independently and resilient to its own failures:
        a bad/halted symbol retries with backoff and is eventually dropped
        WITHOUT cancelling its siblings (``return_exceptions=True``) — one bad
        symbol can't take the whole venue offline. The loop only returns (→ full
        reconnect) once every symbol has given up, i.e. the connection is truly
        dead. A heartbeat keeps the health key fresh while connected.
        """
        if self._client is None:
            raise RuntimeError("client not built")

        async def _watch_one(symbol: str) -> None:
            assert self._client is not None
            backoff = _RECONNECT_INITIAL_S
            failures = 0
            while not self._stopping.is_set():
                try:
                    t0 = time.time()
                    ticker = await self._client.watch_ticker(symbol)
                    self._last_message_at = time.time()
                    self._last_latency_ms = int((time.time() - t0) * 1000)
                    self._record_latency(self._last_latency_ms)
                    tick = self._to_exchange_tick(symbol, ticker)
                    if tick is not None:
                        # Fire-and-forget: blocking on the publish future here
                        # would stall this exchange's whole watch fan-out.
                        self.publisher.publish_nowait(
                            Topic.MARKET_DATA,
                            tick,
                            attributes={"exchange": tick.exchange, "symbol": tick.symbol},
                        )
                    backoff, failures = _RECONNECT_INITIAL_S, 0  # recovered
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures += 1
                    logger.exception(
                        "%s watch failed for %s (failure %d/%d)",
                        self.config.exchange, symbol, failures, _MAX_SYMBOL_FAILURES,
                    )
                    if failures >= _MAX_SYMBOL_FAILURES:
                        logger.error(
                            "%s giving up on symbol %s after %d failures (siblings continue)",
                            self.config.exchange, symbol, failures,
                        )
                        return
                    await asyncio.sleep(backoff)
                    backoff = min(_RECONNECT_MAX_S, backoff * 2)

        async def _heartbeat() -> None:
            # Keep the health key alive while connected, even if symbols are quiet.
            while not self._stopping.is_set():
                await asyncio.sleep(_HEALTH_HEARTBEAT_S)
                self._record_latency(self._last_latency_ms)

        watchers = [asyncio.create_task(_watch_one(s)) for s in self.config.symbols]
        heartbeat = asyncio.create_task(_heartbeat())
        try:
            await asyncio.gather(*watchers, return_exceptions=True)
        finally:
            heartbeat.cancel()
            for t in watchers:
                t.cancel()
            await asyncio.gather(heartbeat, *watchers, return_exceptions=True)

    def _record_latency(self, latency_ms: int) -> None:
        if self.redis is None:
            return
        try:
            self.redis.setex(
                f"{_HEALTH_KEY_PREFIX}{self.config.exchange}",
                30,
                latency_ms,
            )
        except Exception:
            logger.exception("failed to record latency for %s", self.config.exchange)

    def _to_exchange_tick(self, symbol: str, ticker: dict[str, Any]) -> ExchangeTick | None:
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return None
        try:
            canonical = normalize_symbol(self.config.exchange, symbol, self.config.instrument_type)
        except ValueError:
            # An unparseable/misconfigured symbol must not crash the watcher —
            # skip it (the per-symbol loop will count failures and drop it).
            logger.warning("cannot normalize symbol %r on %s — skipping",
                           symbol, self.config.exchange)
            return None
        base = canonical.split("/", 1)[0]
        ts_ms = ticker.get("timestamp") or int(time.time() * 1000)
        return ExchangeTick(
            exchange=self.config.exchange,
            symbol=canonical,
            asset=base,
            bid=float(bid),
            ask=float(ask),
            bid_size=float(ticker.get("bidVolume") or 0.0),
            ask_size=float(ticker.get("askVolume") or 0.0),
            last=float(ticker["last"]) if ticker.get("last") else None,
            timestamp=datetime.fromtimestamp(ts_ms / 1000),
            instrument_type=self.config.instrument_type,
        )


def _enable_rate_limit() -> bool:
    return os.environ.get("CCXT_ENABLE_RATE_LIMIT", "1") == "1"
