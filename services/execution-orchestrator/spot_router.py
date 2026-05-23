"""Spot-leg routing with failover.

The opportunity engine picks the cheapest spot venue for the long leg of a
funding-rate arb. If that primary venue is unhealthy at the moment of
execution, we transparently re-route the spot leg to a fallback venue
(typically Kraken). The perp leg is NOT failed-over — perps are venue-
specific and rerouting them would change the funding rate.

Health signal: ``health:latency:<exchange>`` written by the market-data
service. The orchestrator only READS those keys; only market-data writes
them (matches the CLAUDE.md ownership rule for ``health:*``).

The failover rule is intentionally narrow:
  * If the spot leg is on PRIMARY_SPOT_EXCHANGE and primary is unhealthy,
    rewrite the long_exchange to FALLBACK_SPOT_EXCHANGE.
  * Otherwise leave the opportunity unmodified.

This module is pure-by-default: ``route_for_execution`` takes a Redis
client and an Opportunity and returns the (possibly rewritten) Opportunity
along with a flag indicating whether failover fired.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shared.models.opportunity import Opportunity

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


PRIMARY_SPOT_EXCHANGE = os.environ.get("PRIMARY_SPOT_EXCHANGE", "coinbase")
FALLBACK_SPOT_EXCHANGE = os.environ.get("FALLBACK_SPOT_EXCHANGE", "kraken")

# Anything above this latency is "unhealthy enough to fail over". Stays
# below the risk-engine's reject threshold (500ms) so we can still get
# the trade out the door on the fallback before the engine vetoes the
# primary.
DEFAULT_FAILOVER_LATENCY_MS = 400.0

PREFIX_LATENCY = "health:latency:"


@dataclass(frozen=True)
class RoutingDecision:
    opportunity: Opportunity
    failed_over: bool
    reason: str | None


def _latency_ms(redis: Redis, exchange: str) -> float | None:
    raw = redis.get(f"{PREFIX_LATENCY}{exchange}")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _is_healthy(redis: Redis, exchange: str, max_latency_ms: float) -> bool:
    latency = _latency_ms(redis, exchange)
    if latency is None:
        # No signal at all → treat as unhealthy (fail closed, consistent
        # with the risk-engine's exchange_health rule).
        return False
    return latency <= max_latency_ms


def route_for_execution(
    redis: Redis,
    opportunity: Opportunity,
    max_latency_ms: float = DEFAULT_FAILOVER_LATENCY_MS,
) -> RoutingDecision:
    """Inspect the long (spot) leg and fail-over if the primary is unhealthy."""
    long_ex = opportunity.long_exchange.lower()
    if long_ex != PRIMARY_SPOT_EXCHANGE:
        return RoutingDecision(opportunity=opportunity, failed_over=False, reason=None)

    if _is_healthy(redis, PRIMARY_SPOT_EXCHANGE, max_latency_ms):
        return RoutingDecision(opportunity=opportunity, failed_over=False, reason=None)

    if not _is_healthy(redis, FALLBACK_SPOT_EXCHANGE, max_latency_ms):
        # Fallback is also unhealthy — don't rewrite; let the risk-engine veto.
        logger.warning(
            "spot failover unavailable: both %s and %s unhealthy",
            PRIMARY_SPOT_EXCHANGE,
            FALLBACK_SPOT_EXCHANGE,
        )
        return RoutingDecision(
            opportunity=opportunity,
            failed_over=False,
            reason="fallback also unhealthy",
        )

    rewritten = dataclasses.replace(opportunity, long_exchange=FALLBACK_SPOT_EXCHANGE) \
        if dataclasses.is_dataclass(opportunity) \
        else opportunity.model_copy(update={"long_exchange": FALLBACK_SPOT_EXCHANGE})

    logger.warning(
        "spot failover: opp=%s long_leg %s → %s (primary unhealthy)",
        opportunity.id,
        PRIMARY_SPOT_EXCHANGE,
        FALLBACK_SPOT_EXCHANGE,
    )
    return RoutingDecision(
        opportunity=rewritten,
        failed_over=True,
        reason=f"{PRIMARY_SPOT_EXCHANGE} unhealthy; routed to {FALLBACK_SPOT_EXCHANGE}",
    )
