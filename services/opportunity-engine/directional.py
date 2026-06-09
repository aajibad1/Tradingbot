"""Signal → DIRECTIONAL opportunity connector (hybrid system).

Converts a movement signal (from signal-engine's schema) into a DIRECTIONAL
Opportunity — a single-sided perp bet. Downstream it flows through the advisory
ranker/debate and is gated by the risk-engine's directional-sleeve budget
(``max_directional_exposure_pct``, 0 by default = refused). The neutral arb
strategies are unaffected.

Net edge for a directional bet = expected move (the signal's gross_edge_bps)
minus a ROUND-TRIP cost on the single venue (entry + exit taker fee + slippage +
buffer). There is no funding/convergence term — directional has no guaranteed mean.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from shared.models.opportunity import Opportunity, StrategyType
from shared.utils.fee_calculator import calculate_net_edge, taker_fee_bps
from shared.utils.slippage_model import estimate_size_tier_bps

_DEFAULT_SIZE_USD = 10_000.0
_DEFAULT_VENUE = "hyperliquid"   # perp venue for the directional leg


def opportunity_from_signal(
    signal: dict,
    venue: str = _DEFAULT_VENUE,
    size_usd: float = _DEFAULT_SIZE_USD,
    now: datetime | None = None,
) -> Opportunity:
    """Build a DIRECTIONAL Opportunity from a signal dict.

    Expected signal keys: symbol, direction ('long'|'short'), gross_edge_bps,
    confidence, expiry_ms (optional). ``taker_fee_bps`` raises on an unknown
    venue — fail loud, never silently default a fee.
    """
    direction = signal["direction"]
    if direction not in ("long", "short"):
        raise ValueError(f"directional signal needs direction long/short, got {direction!r}")

    symbol = signal["symbol"]
    asset = symbol.split("/", 1)[0]
    fee = taker_fee_bps(venue)
    slip = estimate_size_tier_bps(size_usd)

    # Round-trip on ONE venue: entry + exit ⇒ fee/slippage counted on both "legs".
    breakdown = calculate_net_edge(
        gross_spread_bps=float(signal["gross_edge_bps"]),
        long_exchange_taker_fee_bps=fee,
        short_exchange_taker_fee_bps=fee,
        slippage_long_bps=slip,
        slippage_short_bps=slip,
    )

    expiry_ms = float(signal.get("expiry_ms", 0) or 0)
    min_hold_hours = max(0.05, expiry_ms / 3_600_000.0)  # ms → hours, small floor

    return Opportunity(
        id=str(uuid.uuid4()),
        strategy=StrategyType.DIRECTIONAL,
        asset=asset,
        long_exchange=venue,      # single-venue perp; direction below disambiguates side
        short_exchange=venue,
        gross_spread_bps=float(signal["gross_edge_bps"]),
        trading_fees_bps=2 * fee,
        slippage_estimate_bps=2 * slip,
        net_edge_bps=breakdown["net_edge_bps"],
        confidence_score=float(signal.get("confidence", 0.5)),
        recommended_size_usd=size_usd,
        min_hold_hours=min_hold_hours,
        detected_at=now or datetime.utcnow(),
        execute=False,
        direction=direction,
    )
