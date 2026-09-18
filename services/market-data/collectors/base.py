"""Base collector. Concrete subclasses wire in a CCXT Pro exchange client and
override `_symbols()` to pick which markets to watch.

The collector owns the connection: one persistent WebSocket per exchange,
reconnect with exponential backoff, normalize ticks, publish to Pub/Sub.

Latency observations are also pushed into Redis so the risk-engine can read
them via `check_exchange_health`.

Config (env):
  VENUE_ANOMALY_URL — opt-in: on each heartbeat, POST this venue's staleness +
  spread to venue-anomaly-detector's /detect (fail-soft; a push failure never
  disrupts the watch loop). See venue_signals() for what is/isn't reported.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from shared.connectors import VenueHealth
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
    region: str = "global"         # shared.connectors region tag
    market_type: str = "cex"       # shared.connectors market-type tag


class BaseCollector:
    """A CCXT-Pro MarketDataAdapter (see shared.connectors.adapter). Native /
    on-chain / Africa-rail collectors implement the same contract."""

    name: str

    def __init__(self, config: CollectorConfig, redis: Redis | None = None) -> None:
        self.config = config
        self.redis = redis
        # MarketDataAdapter contract attributes.
        self.venue = config.exchange
        self.region = config.region
        self.market_type = config.market_type
        self.publisher = get_publisher()
        self._stopping = asyncio.Event()
        self._last_message_at: float = 0.0
        self._last_latency_ms: int = 0
        self._last_bid: float | None = None
        self._last_ask: float | None = None
        self._client: Any | None = None

    def is_healthy(self) -> bool:
        # No message for 60s = stale connection.
        return (time.time() - self._last_message_at) < 60.0

    def health(self) -> VenueHealth:
        """MarketDataAdapter contract: structured health for the venue."""
        return VenueHealth(venue=self.venue, healthy=self.is_healthy(),
                           latency_ms=self._last_latency_ms or None)

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
                # CCXT Pro requires the market map before watch_ticker/orderbook —
                # without this, every watch fails with "<exchange> markets not loaded".
                await self._client.load_markets()
                backoff = _RECONNECT_INITIAL_S  # reset on successful (re)connect
                logger.info("%s connected (%d markets), watching %s",
                            self.config.exchange, len(self._client.markets or {}), self.config.symbols)
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
                        self._last_bid, self._last_ask = tick.bid, tick.ask
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
                await self._push_anomaly_signals()

        watchers = [asyncio.create_task(_watch_one(s)) for s in self.config.symbols]
        heartbeat = asyncio.create_task(_heartbeat())
        try:
            await asyncio.gather(*watchers, return_exceptions=True)
        finally:
            heartbeat.cancel()
            for t in watchers:
                t.cancel()
            await asyncio.gather(heartbeat, *watchers, return_exceptions=True)

    def venue_signals(self, now: float | None = None) -> dict[str, Any] | None:
        """The two feed-health signals this collector can measure with
        confidence: staleness (time since the last message) and spread (from
        the last-seen tick). None before the first tick arrives — there is
        nothing to report yet, not a zero-staleness feed.

        Deliberately omits ``expected_spread_bps``/``sequence_gap_rate``/
        ``update_rate_ratio``/``rejection_rate`` — this collector has no
        reliable baseline for any of them, and venue-anomaly-detector's
        ``detect()`` treats an omitted signal as "no evidence of an anomaly
        here" (safe defaults), never as a false floor/ceiling."""
        if self._last_bid is None or self._last_ask is None:
            return None
        now = now if now is not None else time.time()
        mid = (self._last_bid + self._last_ask) / 2.0
        spread_bps = (self._last_ask - self._last_bid) / mid * 10_000.0 if mid > 0 else 0.0
        return {
            "venue": self.config.exchange,
            "quote_staleness_ms": max(0.0, (now - self._last_message_at) * 1000.0),
            "spread_bps": spread_bps,
        }

    async def _push_anomaly_signals(self) -> None:
        """Push this venue's feed-health signals to venue-anomaly-detector
        (opt-in via VENUE_ANOMALY_URL; fail-soft — a reporting hiccup must
        never disrupt the collector's own watch loop)."""
        base = os.environ.get("VENUE_ANOMALY_URL")
        if not base:
            return
        signals = self.venue_signals()
        if signals is None:
            return
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(f"{base.rstrip('/')}/detect", json=signals)
        except Exception:
            logger.warning("venue-anomaly-detector push failed for %s", self.config.exchange)

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
