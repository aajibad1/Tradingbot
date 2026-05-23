"""Combine the three sentiment sources into one ``SentimentSignal``.

Combining rule (block if ANY trip; fail OPEN on missing data):

  * fear_greed_index  < SENTIMENT_FG_BLOCK_THRESHOLD     (default 25)
  * perplexity_score  < SENTIMENT_SCORE_BLOCK_THRESHOLD  (default -0.1)
  * cryptopanic_score > SENTIMENT_PANIC_BLOCK_THRESHOLD  (default 70)

"Fail OPEN" means: if a source raises or returns None, we do NOT count it
as a block — sentiment is a tiebreaker, not a primary risk control. The
liquidation monitor and drawdown guard are the real backstops.

The aggregator is async because the underlying clients are httpx-async;
calls fan out concurrently to keep the 4h refresh latency in the
single-digit-seconds range.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

from models import SentimentSignal

if TYPE_CHECKING:
    from clients.base import SentimentClient
    from clients.cryptopanic import CryptoPanicResult
    from clients.fear_greed import FearGreedResult
    from clients.perplexity import PerplexityResult

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("ignoring bad %s=%r; using default %s", name, raw, default)
        return default


def fg_threshold() -> float:
    return _env_float("SENTIMENT_FG_BLOCK_THRESHOLD", 25.0)


def perplexity_threshold() -> float:
    return _env_float("SENTIMENT_SCORE_BLOCK_THRESHOLD", -0.1)


def cryptopanic_threshold() -> float:
    return _env_float("SENTIMENT_PANIC_BLOCK_THRESHOLD", 70.0)


async def _safe_fetch(client: SentimentClient):
    """Run a client and trap exceptions — sentinel for fail-open behaviour."""
    try:
        return await client.fetch()
    except Exception as e:  # noqa: BLE001 — fail-open on every source failure
        logger.warning("sentiment source %s failed: %s", client.name, e)
        return None


def _evaluate_block(
    fg: FearGreedResult | None,
    px: PerplexityResult | None,
    cp: CryptoPanicResult | None,
) -> tuple[bool, list[str]]:
    """Apply the threshold rule and return (is_bearish, reasons)."""
    reasons: list[str] = []
    fg_t = fg_threshold()
    px_t = perplexity_threshold()
    cp_t = cryptopanic_threshold()

    if fg is not None and fg.index < fg_t:
        reasons.append(
            f"fear_greed_index {fg.index} < {fg_t:.0f} ({fg.classification})"
        )
    if px is not None and px.score < px_t:
        reasons.append(
            f"perplexity_score {px.score:.2f} < {px_t:.2f}"
        )
    if cp is not None and cp.panic_score > cp_t:
        reasons.append(
            f"cryptopanic_panic_score {cp.panic_score:.1f} > {cp_t:.0f} "
            f"({cp.articles_24h} articles in 24h)"
        )
    return bool(reasons), reasons


async def aggregate(
    fear_greed_client: SentimentClient | None,
    perplexity_client: SentimentClient | None,
    cryptopanic_client: SentimentClient | None,
) -> SentimentSignal:
    """Fan out all configured sources concurrently and combine the result."""
    tasks = []
    keys: list[str] = []
    if fear_greed_client is not None:
        tasks.append(_safe_fetch(fear_greed_client))
        keys.append("fear_greed")
    if perplexity_client is not None:
        tasks.append(_safe_fetch(perplexity_client))
        keys.append("perplexity")
    if cryptopanic_client is not None:
        tasks.append(_safe_fetch(cryptopanic_client))
        keys.append("cryptopanic")

    results = await asyncio.gather(*tasks) if tasks else []

    by_key = dict(zip(keys, results))
    fg = by_key.get("fear_greed")
    px = by_key.get("perplexity")
    cp = by_key.get("cryptopanic")

    is_bearish, reasons = _evaluate_block(fg, px, cp)

    return SentimentSignal(
        observed_at=datetime.utcnow(),
        fear_greed_index=fg.index if fg is not None else None,
        fear_greed_classification=fg.classification if fg is not None else None,
        perplexity_score=px.score if px is not None else None,
        perplexity_summary=px.summary if px is not None else None,
        cryptopanic_panic_score=cp.panic_score if cp is not None else None,
        cryptopanic_articles_24h=cp.articles_24h if cp is not None else None,
        is_bearish=is_bearish,
        block_reasons=reasons,
        sources_ok={
            "fear_greed": fg is not None,
            "perplexity": px is not None,
            "cryptopanic": cp is not None,
        },
    )
