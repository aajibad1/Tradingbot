"""Opportunity scorer — wires net edge + slippage + confidence into one shape.

The scorer never re-implements the math from `shared/utils/fee_calculator`.
It only orchestrates: takes a candidate (assembled by a strategy) and produces
a fully-populated Opportunity ready for downstream consumers.

The dynamic viability threshold is resolved through
``shared.utils.fee_calculator.min_viable_net_edge_bps()``, which reads
``opportunity:dynamic_min_spread`` from Redis when available and otherwise
returns the static ``MIN_VIABLE_NET_EDGE_BPS`` floor. The scorer marks
non-viable opportunities with ``rejection_reason`` so downstream filtering
and observability can see why a candidate was dropped. Filtering itself
remains the publisher's job in ``main.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from shared.models.opportunity import Opportunity, StrategyType
from shared.utils.fee_calculator import (
    calculate_net_edge,
    min_viable_net_edge_bps,
    taker_fee_bps,
)


@dataclass
class ScoredCandidate:
    """Raw inputs a strategy hands the scorer."""

    strategy: StrategyType
    asset: str
    long_exchange: str
    short_exchange: str
    gross_spread_bps: float
    slippage_long_bps: float
    slippage_short_bps: float
    funding_rate_bps_per_period: float = 0.0
    funding_rate_annualized_pct: float = 0.0
    recommended_size_usd: float = 10_000.0
    min_hold_hours: float = 4.0
    confidence_score: float = 0.5


def score(candidate: ScoredCandidate, now: datetime | None = None) -> Opportunity:
    long_fee = taker_fee_bps(candidate.long_exchange)
    short_fee = taker_fee_bps(candidate.short_exchange)
    threshold = min_viable_net_edge_bps()
    breakdown = calculate_net_edge(
        gross_spread_bps=candidate.gross_spread_bps,
        long_exchange_taker_fee_bps=long_fee,
        short_exchange_taker_fee_bps=short_fee,
        slippage_long_bps=candidate.slippage_long_bps,
        slippage_short_bps=candidate.slippage_short_bps,
        funding_rate_bps_per_period=candidate.funding_rate_bps_per_period,
        min_viable_net_edge_bps=threshold,
    )
    rejection: str | None = None
    if not breakdown["is_viable"]:
        rejection = (
            f"net_edge {breakdown['net_edge_bps']:.2f} bps below "
            f"threshold {threshold:.2f} bps"
        )
    return Opportunity(
        id=str(uuid.uuid4()),
        strategy=candidate.strategy,
        asset=candidate.asset,
        long_exchange=candidate.long_exchange,
        short_exchange=candidate.short_exchange,
        gross_spread_bps=breakdown["gross_spread_bps"],
        trading_fees_bps=long_fee + short_fee,
        slippage_estimate_bps=candidate.slippage_long_bps + candidate.slippage_short_bps,
        funding_rate_annualized_pct=candidate.funding_rate_annualized_pct,
        net_edge_bps=breakdown["net_edge_bps"],
        confidence_score=candidate.confidence_score,
        recommended_size_usd=candidate.recommended_size_usd,
        min_hold_hours=candidate.min_hold_hours,
        detected_at=now or datetime.utcnow(),
        execute=False,  # Set True only after risk-engine approves
        rejection_reason=rejection,
    )
