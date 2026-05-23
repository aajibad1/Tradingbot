"""Net-edge calculation. Single source of truth for opportunity viability.

The viability threshold is intentionally conservative. The default
``MIN_VIABLE_NET_EDGE_BPS = 50.0`` reflects an empirical breakdown observed
on funding-rate-arb trades sized at $10K:

  * round-trip taker fees on a kraken / hyperliquid pair: ~15 bps
  * order-book slippage at $10K notional:                  ~10 bps
  * 3 bp safety buffer (DEFAULT_SAFETY_BUFFER_BPS)

Setting the bar at 50 bps leaves ~25 bps of true net edge after costs.
Lowering this without a corresponding fee-tier or slippage improvement is
a profitability bug; the codebase deliberately fails loud at this value.

A dynamic override is supported via ``min_viable_net_edge_bps()`` which
reads ``opportunity:dynamic_min_spread`` from Redis when set by a fee-tier
tracker (see ``shared/utils/fee_tracker.py``). Callers that want the
runtime-tunable threshold should call that helper instead of reading the
module-level constant directly.
"""

from __future__ import annotations

import logging
import os
from typing import TypedDict

logger = logging.getLogger(__name__)

MIN_VIABLE_NET_EDGE_BPS = 50.0
DEFAULT_SAFETY_BUFFER_BPS = 3.0

# Redis key — written by the fee-tier tracker (shared/utils/fee_tracker.py)
# every 24h with a blended-fee-aware threshold. Read by the opportunity
# scorer and the risk-engine when deciding viability. Always *bps* (float).
DYNAMIC_MIN_EDGE_REDIS_KEY = "opportunity:dynamic_min_spread"

# Base-tier taker fees in bps (1 bp = 0.01%). Source: docs/EXCHANGE_FEES.md.
EXCHANGE_TAKER_FEE_BPS: dict[str, float] = {
    "coinbase": 60.0,
    "kraken": 26.0,
    "crypto.com": 7.5,
    "binance.us": 10.0,
    "hyperliquid": 5.0,
}


class CostBreakdown(TypedDict):
    long_fee: float
    short_fee: float
    slippage_long: float
    slippage_short: float
    transfer: float
    borrow: float
    buffer: float


class NetEdgeResult(TypedDict):
    gross_spread_bps: float
    total_cost_bps: float
    net_edge_bps: float
    is_viable: bool
    cost_breakdown: CostBreakdown


def calculate_net_edge(
    gross_spread_bps: float,
    long_exchange_taker_fee_bps: float,
    short_exchange_taker_fee_bps: float,
    slippage_long_bps: float,
    slippage_short_bps: float,
    funding_rate_bps_per_period: float = 0.0,
    transfer_cost_bps: float = 0.0,
    borrow_cost_bps: float = 0.0,
    safety_buffer_bps: float = DEFAULT_SAFETY_BUFFER_BPS,
    min_viable_net_edge_bps: float | None = None,
) -> NetEdgeResult:
    """Compute net edge and full cost breakdown.

    ``is_viable`` is True when ``net_edge_bps > threshold``, where the
    threshold defaults to the module-level ``MIN_VIABLE_NET_EDGE_BPS`` but
    can be overridden per-call (callers that want the Redis-backed dynamic
    value should resolve it via ``min_viable_net_edge_bps()`` and pass it
    in). The caller is also expected to check persistence (>= 30 min) and
    confidence separately.
    """
    total_cost_bps = (
        long_exchange_taker_fee_bps
        + short_exchange_taker_fee_bps
        + slippage_long_bps
        + slippage_short_bps
        + transfer_cost_bps
        + borrow_cost_bps
        + safety_buffer_bps
    )
    net_edge_bps = gross_spread_bps + funding_rate_bps_per_period - total_cost_bps
    threshold = (
        min_viable_net_edge_bps
        if min_viable_net_edge_bps is not None
        else MIN_VIABLE_NET_EDGE_BPS
    )

    return {
        "gross_spread_bps": gross_spread_bps,
        "total_cost_bps": total_cost_bps,
        "net_edge_bps": net_edge_bps,
        "is_viable": net_edge_bps > threshold,
        "cost_breakdown": {
            "long_fee": long_exchange_taker_fee_bps,
            "short_fee": short_exchange_taker_fee_bps,
            "slippage_long": slippage_long_bps,
            "slippage_short": slippage_short_bps,
            "transfer": transfer_cost_bps,
            "borrow": borrow_cost_bps,
            "buffer": safety_buffer_bps,
        },
    }


def taker_fee_bps(exchange: str) -> float:
    """Lookup base-tier taker fee. Raises KeyError on unknown exchange — fail loud."""
    return EXCHANGE_TAKER_FEE_BPS[exchange.lower()]


def min_viable_net_edge_bps() -> float:
    """Resolve the current minimum net-edge threshold.

    Order of precedence:
      1. Redis key ``opportunity:dynamic_min_spread`` (bps as float), if
         REDIS_URL is set and the key is present.
      2. Module-level ``MIN_VIABLE_NET_EDGE_BPS`` (default 50.0 bps).

    Never silently falls below the static default — if the Redis value is
    lower we still return the larger of the two. That preserves the
    invariant: dynamic-threshold updates can only TIGHTEN viability, never
    loosen it.
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return MIN_VIABLE_NET_EDGE_BPS
    try:
        from redis import Redis  # local import to keep test envs lightweight

        client = Redis.from_url(redis_url, decode_responses=True)
        raw = client.get(DYNAMIC_MIN_EDGE_REDIS_KEY)
        if raw is None:
            return MIN_VIABLE_NET_EDGE_BPS
        dynamic = float(raw)
        # Tighten-only: never drop below the static floor.
        return max(MIN_VIABLE_NET_EDGE_BPS, dynamic)
    except Exception:
        # Redis transient blip or bad value — fall back to the static default
        # rather than blocking the hot path.
        logger.exception("dynamic min-edge lookup failed; falling back to static")
        return MIN_VIABLE_NET_EDGE_BPS
