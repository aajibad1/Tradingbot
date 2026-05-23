"""Market-data service.

Spawns one async collector per (exchange, instrument-type) pair, each running
in its own task with reconnect logic. The FastAPI surface exists only for
Cloud Run liveness — there are no inbound calls in normal operation.

Symbols are configured per exchange via env var MARKET_DATA_CONFIG (JSON),
e.g.:
  {
    "coinbase":     {"spot": ["BTC/USD", "ETH/USD"]},
    "kraken":       {"spot": ["XBT/USD"], "perp": ["BTC/USD:USD"]},
    "crypto.com":   {"spot": ["BTC_USDT"], "perp": ["BTC_USDT-PERP"]},
    "binance.us":   {"spot": ["BTCUSD"]},
    "hyperliquid":  {"perp": ["BTC", "ETH"]}
  }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from collectors import (
    BaseCollector,
    BinanceUSCollector,
    CoinbaseCollector,
    CollectorConfig,
    CryptoComCollector,
    HyperliquidCollector,
    KrakenCollector,
)

if TYPE_CHECKING:
    from redis import Redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("market-data")


_COLLECTOR_REGISTRY: dict[str, type[BaseCollector]] = {
    "coinbase": CoinbaseCollector,
    "kraken": KrakenCollector,
    "crypto.com": CryptoComCollector,
    "binance.us": BinanceUSCollector,
    "hyperliquid": HyperliquidCollector,
}


def _build_collectors(redis: Redis | None) -> list[BaseCollector]:
    raw = os.environ.get("MARKET_DATA_CONFIG", "{}")
    config = json.loads(raw)
    if not config:
        logger.warning("MARKET_DATA_CONFIG unset — no collectors will run")
    collectors: list[BaseCollector] = []
    for exchange, by_type in config.items():
        cls = _COLLECTOR_REGISTRY.get(exchange)
        if cls is None:
            logger.warning("unknown exchange in config: %s — skipping", exchange)
            continue
        for instrument_type, symbols in by_type.items():
            if not symbols:
                continue
            collectors.append(
                cls(
                    CollectorConfig(
                        exchange=exchange,
                        symbols=symbols,
                        instrument_type=instrument_type,
                    ),
                    redis=redis,
                )
            )
    return collectors


def _build_redis() -> Redis | None:
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    from redis import Redis

    return Redis.from_url(url, decode_responses=True)


_collectors: list[BaseCollector] = []
_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _collectors, _tasks
    redis = _build_redis()
    _collectors = _build_collectors(redis)
    _tasks = [asyncio.create_task(c.run(), name=f"collector-{c.config.exchange}") for c in _collectors]
    logger.info("started %d collectors", len(_collectors))
    try:
        yield
    finally:
        for c in _collectors:
            await c.stop()
        for t in _tasks:
            t.cancel()
        await asyncio.gather(*_tasks, return_exceptions=True)


app = FastAPI(title="market-data", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, object]:
    status_by_exchange = {}
    for c in _collectors:
        status_by_exchange[c.config.exchange] = {
            "healthy": c.is_healthy(),
            "last_message_at": c._last_message_at if c._last_message_at > 0 else None,
            "symbols": c.config.symbols,
        }

    healthy = [ex for ex, s in status_by_exchange.items() if s["healthy"]]
    unhealthy = [ex for ex, s in status_by_exchange.items() if not s["healthy"]]

    return {
        "status": "ok" if not unhealthy else "degraded",
        "healthy": healthy,
        "unhealthy": unhealthy,
        "exchanges": status_by_exchange,
    }
