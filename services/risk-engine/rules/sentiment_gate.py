"""Sentiment-based opportunity gate.

Reads the ``sentiment:*`` Redis namespace written by the sentiment-service
on a 4-hour cadence. When the latest signal is bearish, this rule emits a
RiskViolation that causes ``/evaluate`` to reject the opportunity.

**Fail-OPEN semantics** (deliberate, matches the sentiment-service design):

  * If ``sentiment:is_bearish`` is absent → no violation (signal expired
    or refresh job missed; we don't block trading on missing observability).
  * If Redis itself is unreachable → no violation (the function returns
    an empty list and logs; the kill-switch and drawdown guards remain
    the authoritative backstops).
  * If ``sentiment:is_bearish`` is "0" → no violation.
  * If ``sentiment:is_bearish`` is "1" → ONE RiskViolation, with
    ``message`` constructed from ``sentiment:block_reasons``.

Sentiment is a soft gate, not a kill switch. It never trips the kill
switch; the operator can clear a single bearish window by waiting for the
next refresh or by manually overwriting the Redis key (audit-logged via
the existing risk-alert publisher).

Risk-engine never WRITES to ``sentiment:*`` — sentiment-service is the
sole writer of that namespace.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from shared.models.risk_state import RiskRule, RiskViolation

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


KEY_IS_BEARISH = "sentiment:is_bearish"
KEY_BLOCK_REASONS = "sentiment:block_reasons"
KEY_OBSERVED_AT = "sentiment:observed_at"


def check_sentiment(redis: Redis) -> list[RiskViolation]:
    """Return at most one RiskViolation when the sentiment signal is bearish.

    Fail-open: any error reading Redis or parsing the value returns ``[]``.
    """
    try:
        raw = redis.get(KEY_IS_BEARISH)
    except Exception:  # noqa: BLE001 — fail open on transport errors
        logger.exception("sentiment redis read failed; failing open")
        return []

    if raw is None or raw == "0":
        return []
    if raw != "1":
        logger.warning("unexpected sentiment:is_bearish value: %r (failing open)", raw)
        return []

    # Build a human-readable rejection message from the structured reasons.
    reasons: list[str] = []
    try:
        reasons_raw = redis.get(KEY_BLOCK_REASONS)
        if reasons_raw:
            parsed = json.loads(reasons_raw)
            if isinstance(parsed, list):
                reasons = [str(r) for r in parsed]
    except Exception:  # noqa: BLE001 — best-effort, the gate still trips
        logger.exception("could not parse sentiment:block_reasons")

    try:
        observed_at = redis.get(KEY_OBSERVED_AT) or "unknown"
    except Exception:  # noqa: BLE001
        observed_at = "unknown"

    if reasons:
        joined = "; ".join(reasons)
        message = f"sentiment bearish (observed_at={observed_at}): {joined}"
    else:
        message = f"sentiment bearish (observed_at={observed_at})"

    # ``RiskRule.MIN_NET_EDGE`` is the closest existing enum value: this is a
    # soft gate that says "the expected edge is being eroded by macro
    # conditions". Reusing the existing enum avoids touching shared/models.
    # The message string is unambiguous about the actual cause.
    return [
        RiskViolation(
            rule=RiskRule.MIN_NET_EDGE,
            observed=1.0,
            limit=0.0,
            message=message,
        )
    ]
