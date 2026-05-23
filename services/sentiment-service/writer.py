"""Redis writer for the ``sentiment:*`` namespace.

Schema (sentiment-service is the SOLE WRITER of these keys):

    sentiment:is_bearish                "1" / "0"
    sentiment:block_reasons             JSON list of strings
    sentiment:fear_greed_index          int 0..100  (absent when source failed)
    sentiment:fear_greed_classification string      (absent when source failed)
    sentiment:perplexity_score          float -1..1 (absent when source failed)
    sentiment:perplexity_summary        string      (absent when source failed)
    sentiment:cryptopanic_panic_score   float 0..100 (absent when source failed)
    sentiment:cryptopanic_articles_24h  int          (absent when source failed)
    sentiment:observed_at               ISO-8601
    sentiment:sources_ok                JSON object {source: bool}

All keys are written with a TTL slightly longer than the refresh cadence so
that a refresh-job failure causes the gate to fail OPEN (keys expire,
risk-engine treats absence as "no bearish signal"). Default TTL is 5h
when the refresh runs every 4h — one full cycle of grace.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from models import SentimentSignal

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


NAMESPACE = "sentiment"
DEFAULT_TTL_S = 5 * 60 * 60  # 5 hours — one refresh cycle plus a buffer


def _ttl_s() -> int:
    raw = os.environ.get("SENTIMENT_REDIS_TTL_S")
    if raw is None:
        return DEFAULT_TTL_S
    try:
        return int(raw)
    except ValueError:
        logger.warning("ignoring bad SENTIMENT_REDIS_TTL_S=%r", raw)
        return DEFAULT_TTL_S


def write_signal(redis: Redis, signal: SentimentSignal) -> int:
    """Persist the signal to Redis. Returns the TTL applied (seconds).

    Uses SETEX so every key expires on its own; we never have to clean up.
    """
    ttl = _ttl_s()

    def set_(suffix: str, value) -> None:
        redis.setex(f"{NAMESPACE}:{suffix}", ttl, value)

    def set_if(suffix: str, value) -> None:
        """Only write if the value is non-None — absence is part of the contract."""
        if value is None:
            redis.delete(f"{NAMESPACE}:{suffix}")
            return
        redis.setex(f"{NAMESPACE}:{suffix}", ttl, str(value))

    set_("is_bearish", "1" if signal.is_bearish else "0")
    set_("block_reasons", json.dumps(signal.block_reasons))
    set_("observed_at", signal.observed_at.isoformat())
    set_("sources_ok", json.dumps(signal.sources_ok))

    set_if("fear_greed_index", signal.fear_greed_index)
    set_if("fear_greed_classification", signal.fear_greed_classification)
    set_if("perplexity_score", signal.perplexity_score)
    set_if("perplexity_summary", signal.perplexity_summary)
    set_if("cryptopanic_panic_score", signal.cryptopanic_panic_score)
    set_if("cryptopanic_articles_24h", signal.cryptopanic_articles_24h)

    return ttl
