"""sentiment-service — Cloud-Scheduler-driven sentiment refresh.

Cadence: every 4 hours. Cloud Scheduler hits ``POST /sentiment/refresh``,
which fans out to Fear & Greed, Perplexity Sonar, and CryptoPanic
concurrently, combines the result, and writes the ``sentiment:*`` keys to
Redis. The risk-engine reads those keys on every /evaluate call.

Local-dev: the service still starts without GCP credentials. Each source
is constructed only when its API key is available — missing keys
gracefully degrade rather than blocking startup.

Endpoints:
  GET  /healthz                    Cloud Run liveness
  GET  /status                     last refresh metadata + per-source config
  POST /sentiment/refresh          run the refresh cycle now (Cloud Scheduler)
  GET  /sentiment/current          read the latest snapshot from Redis (debugging)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException

from aggregator import aggregate
from clients import CryptoPanicClient, FearGreedClient, PerplexityClient
from models import HealthResponse, RefreshResponse, SentimentSignal
from writer import NAMESPACE, write_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("sentiment-service")


_state_lock = threading.Lock()
_last_refresh_at: datetime | None = None
_last_signal: SentimentSignal | None = None


def _build_clients() -> tuple[
    FearGreedClient | None, PerplexityClient | None, CryptoPanicClient | None
]:
    """Construct each client; skip the ones whose API key is unavailable."""
    fg: FearGreedClient | None = None
    px: PerplexityClient | None = None
    cp: CryptoPanicClient | None = None

    # Fear & Greed is free — always on.
    try:
        fg = FearGreedClient()
    except Exception as e:  # noqa: BLE001
        logger.warning("FearGreedClient init failed: %s", e)

    try:
        px = PerplexityClient()
        logger.info("PerplexityClient initialized")
    except RuntimeError as e:
        logger.warning("PerplexityClient unavailable: %s", e)

    try:
        cp = CryptoPanicClient()
        logger.info("CryptoPanicClient initialized")
    except RuntimeError as e:
        logger.warning("CryptoPanicClient unavailable: %s", e)

    return fg, px, cp


def _get_redis():
    """Lazy import — keeps tests + local dev light."""
    from redis import Redis

    return Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


app = FastAPI(title="sentiment-service", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status", response_model=HealthResponse)
def status() -> HealthResponse:
    fg, px, cp = _build_clients()
    sources_configured = {
        "fear_greed": fg is not None,
        "perplexity": px is not None,
        "cryptopanic": cp is not None,
    }
    with _state_lock:
        return HealthResponse(
            status="ok",
            last_refresh_at=_last_refresh_at,
            last_signal=_last_signal,
            sources_configured=sources_configured,
        )


@app.post("/sentiment/refresh", response_model=RefreshResponse)
async def refresh() -> RefreshResponse:
    """Run a refresh cycle now. Writes sentiment:* keys to Redis."""
    fg, px, cp = _build_clients()
    # Fear & Greed is keyless and therefore always available, but it is too
    # coarse to gate trading on alone. Require at least one *keyed* source
    # (Perplexity or CryptoPanic); otherwise refuse the refresh rather than
    # write a single-source signal the risk-engine would gate on.
    if px is None and cp is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "no sentiment sources configured — set PERPLEXITY_API_KEY and/or "
                "CRYPTOPANIC_API_KEY (Fear & Greed alone is insufficient)"
            ),
        )

    signal = await aggregate(fg, px, cp)

    wrote_redis = False
    ttl_s = 0
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        try:
            redis = _get_redis()
            ttl_s = write_signal(redis, signal)
            wrote_redis = True
        except Exception:  # noqa: BLE001
            logger.exception("failed to write sentiment to redis")
    else:
        logger.info("REDIS_URL unset — skipping redis write (local dev)")

    global _last_refresh_at, _last_signal
    with _state_lock:
        _last_refresh_at = datetime.utcnow()
        _last_signal = signal

    return RefreshResponse(signal=signal, wrote_redis=wrote_redis, redis_ttl_s=ttl_s)


@app.get("/sentiment/current")
def current() -> dict[str, Any]:
    """Read the current ``sentiment:*`` snapshot from Redis (debugging)."""
    if not os.environ.get("REDIS_URL"):
        raise HTTPException(status_code=503, detail="REDIS_URL unset")
    redis = _get_redis()

    def _get(suffix: str) -> str | None:
        return redis.get(f"{NAMESPACE}:{suffix}")

    raw = {
        "is_bearish": _get("is_bearish"),
        "block_reasons": _get("block_reasons"),
        "observed_at": _get("observed_at"),
        "sources_ok": _get("sources_ok"),
        "fear_greed_index": _get("fear_greed_index"),
        "fear_greed_classification": _get("fear_greed_classification"),
        "perplexity_score": _get("perplexity_score"),
        "perplexity_summary": _get("perplexity_summary"),
        "cryptopanic_panic_score": _get("cryptopanic_panic_score"),
        "cryptopanic_articles_24h": _get("cryptopanic_articles_24h"),
    }
    # JSON-decode the structured fields if present so the response is pretty.
    for key in ("block_reasons", "sources_ok"):
        if raw[key] is not None:
            try:
                raw[key] = json.loads(raw[key])
            except (TypeError, ValueError):
                pass
    return raw
