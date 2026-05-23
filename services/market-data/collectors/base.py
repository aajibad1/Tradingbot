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
        """CCXT Pro watchTicker per-symbol fan-out."""
        if self._client is None:
            raise RuntimeError("client not built")

        async def _watch_one(symbol: str) -> None:
            assert self._client is not None
            while not self._stopping.is_set():
                t0 = time.time()
                ticker = await self._client.watch_ticker(symbol)
                self._last_message_at = time.time()
                self._record_latency(int((time.time() - t0) * 1000))
                tick = self._to_exchange_tick(symbol, ticker)
                if tick is not None:
                    self.publisher.publish(
                        Topic.MARKET_DATA,
                        tick,
                        attributes={"exchange": tick.exchange, "symbol": tick.symbol},
                    )

        tasks = [asyncio.create_task(_watch_one(s)) for s in self.config.symbols]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

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
        canonical = normalize_symbol(self.config.exchange, symbol, self.config.instrument_type)
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
