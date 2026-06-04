"""Fee-tier tracker — pure helpers, called from a scheduled job.

Each exchange publishes a fee schedule that gets cheaper as 30-day volume
crosses tier thresholds. The dashboard and the opportunity scorer both want
to know:

  * What's our blended taker fee right now?
  * How far are we from unlocking the next tier?
  * What dynamic ``MIN_VIABLE_NET_EDGE_BPS`` should we publish to Redis?

This module is intentionally I/O-free at import. The two side-effecting
entry points (``update_redis_tier`` and ``update_dynamic_min_edge``) take
a redis-py client explicitly so callers wire in their own connection and
this module can be unit-tested without any redis runtime.

Anchored to the canonical fee numbers in ``docs/EXCHANGE_FEES.md`` and
``shared/utils/fee_calculator.EXCHANGE_TAKER_FEE_BPS``. Tier 0 entries
must match the static fee table — if those numbers diverge, opportunity
viability math will silently disagree with the fee tracker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shared.utils.fee_calculator import (
    DYNAMIC_MIN_EDGE_REDIS_KEY,
    EXCHANGE_TAKER_FEE_BPS,
    MIN_VIABLE_NET_EDGE_BPS,
)

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


# Each entry: list of (min_30d_volume_usd, taker_fee_bps) sorted ascending.
# The tier active at volume V is the entry with the largest min <= V.
# Tier 0 values MUST match EXCHANGE_TAKER_FEE_BPS in fee_calculator.py.
FEE_TIERS_BPS: dict[str, list[tuple[float, float]]] = {
    "coinbase": [
        (0.0, 60.0),
        (10_000.0, 40.0),
        (50_000.0, 25.0),
        (100_000.0, 18.0),
        (1_000_000.0, 5.0),
    ],
    "kraken": [
        (0.0, 26.0),
        (50_000.0, 24.0),
        (100_000.0, 22.0),
        (250_000.0, 20.0),
        (500_000.0, 16.0),
    ],
    "crypto.com": [
        (0.0, 7.5),
        (25_000.0, 7.5),
        (50_000.0, 5.0),
        (100_000.0, 4.0),
    ],
    "binance.us": [
        (0.0, 10.0),
        (50_000.0, 8.0),
        (100_000.0, 7.0),
    ],
    "hyperliquid": [
        (0.0, 5.0),
        (5_000_000.0, 4.5),
    ],
}


REDIS_FEE_KEY_PREFIX = "fees:taker:"
REDIS_DAYS_TO_NEXT_PREFIX = "fees:days_to_next_tier:"
REDIS_VOLUME_PREFIX = "stats:volume_30d:"


@dataclass(frozen=True)
class TierLookup:
    exchange: str
    current_fee_bps: float
    current_tier_min_volume_usd: float
    next_tier_min_volume_usd: float | None
    next_tier_fee_bps: float | None


def tier_for_volume(exchange: str, volume_30d_usd: float) -> TierLookup:
    """Return the active tier and the next-tier hint for an exchange.

    Raises ``KeyError`` for unknown exchanges — same fail-loud contract as
    ``taker_fee_bps``.
    """
    tiers = FEE_TIERS_BPS[exchange.lower()]
    active = tiers[0]
    next_tier: tuple[float, float] | None = None
    for tier in tiers:
        min_vol, _ = tier
        if volume_30d_usd >= min_vol:
            active = tier
        else:
            next_tier = tier
            break
    next_min = next_tier[0] if next_tier is not None else None
    next_fee = next_tier[1] if next_tier is not None else None
    return TierLookup(
        exchange=exchange.lower(),
        current_fee_bps=active[1],
        current_tier_min_volume_usd=active[0],
        next_tier_min_volume_usd=next_min,
        next_tier_fee_bps=next_fee,
    )


def days_to_next_tier(volume_30d_usd: float, next_min_volume_usd: float | None) -> int | None:
    """Linear-trajectory ETA, in days, to the next tier.

    Uses the simple model: at the current 30-day pace, when will we hit the
    next tier? Returns ``None`` if no next tier exists. Returns 0 if we're
    already at or above the next tier (caller already cleared it).
    """
    if next_min_volume_usd is None:
        return None
    if volume_30d_usd <= 0:
        return None  # No trajectory data yet — caller decides what to show.
    if volume_30d_usd >= next_min_volume_usd:
        return 0
    daily_pace = volume_30d_usd / 30.0
    if daily_pace <= 0:
        return None
    remaining = next_min_volume_usd - volume_30d_usd
    return max(0, int(remaining / daily_pace))


def blended_fee_bps(per_exchange_fees: dict[str, float]) -> float:
    """Equal-weight blend across the active per-exchange taker fees.

    Empty input → falls back to a conservative average of the static
    EXCHANGE_TAKER_FEE_BPS, never below the most expensive venue / 2.
    """
    if not per_exchange_fees:
        static = list(EXCHANGE_TAKER_FEE_BPS.values())
        return sum(static) / len(static)
    return sum(per_exchange_fees.values()) / len(per_exchange_fees)


def dynamic_min_edge_bps(blended_bps: float, multiplier: float = 3.0) -> float:
    """Derive the runtime ``MIN_VIABLE_NET_EDGE_BPS`` from the blended fee.

    Rule of thumb: we want the threshold to be at least ``multiplier`` x the
    round-trip blended fee, AND never below the static floor. The static
    floor wins on tie — the override can only *tighten* the bar.
    """
    candidate = blended_bps * 2.0 * multiplier  # 2x for round-trip (long + short legs)
    return max(MIN_VIABLE_NET_EDGE_BPS, candidate)


def update_redis_tier(
    redis: Redis, exchange: str, volume_30d_usd: float
) -> TierLookup:
    """Side-effecting helper: write current fee + days-to-next to Redis.

    The Redis keys follow the prefixes above. The risk-engine and the
    opportunity scorer never write these — only this helper does.
    """
    lookup = tier_for_volume(exchange, volume_30d_usd)
    redis.set(f"{REDIS_FEE_KEY_PREFIX}{lookup.exchange}", lookup.current_fee_bps)
    days = days_to_next_tier(volume_30d_usd, lookup.next_tier_min_volume_usd)
    if days is not None:
        redis.set(f"{REDIS_DAYS_TO_NEXT_PREFIX}{lookup.exchange}", days)
    return lookup


def update_dynamic_min_edge(
    redis: Redis, per_exchange_fees: dict[str, float] | None = None
) -> float:
    """Side-effecting helper: write the dynamic min edge to Redis.

    Returns the bps value written.
    """
    if per_exchange_fees is None:
        per_exchange_fees = {}
        for ex in FEE_TIERS_BPS:
            raw = redis.get(f"{REDIS_FEE_KEY_PREFIX}{ex}")
            if raw is not None:
                try:
                    per_exchange_fees[ex] = float(raw)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    logger.warning("ignoring bad fee value in redis for %s: %r", ex, raw)
    blended = blended_fee_bps(per_exchange_fees)
    new_min = dynamic_min_edge_bps(blended)
    redis.set(DYNAMIC_MIN_EDGE_REDIS_KEY, new_min)
    return new_min


def refresh_all(redis: Redis) -> dict[str, TierLookup]:
    """End-to-end refresh: read 30d volume per exchange, write fees + min edge.

    Returns the per-exchange tier lookups for observability. Intended to be
    called from a 24h scheduled job (Cloud Scheduler → Cloud Run).
    """
    lookups: dict[str, TierLookup] = {}
    per_ex_fees: dict[str, float] = {}
    for ex in FEE_TIERS_BPS:
        raw = redis.get(f"{REDIS_VOLUME_PREFIX}{ex}")
        try:
            volume = float(raw) if raw is not None else 0.0  # type: ignore[arg-type]
        except (TypeError, ValueError):
            volume = 0.0
        lookup = update_redis_tier(redis, ex, volume)
        lookups[ex] = lookup
        per_ex_fees[ex] = lookup.current_fee_bps
    update_dynamic_min_edge(redis, per_ex_fees)
    return lookups
