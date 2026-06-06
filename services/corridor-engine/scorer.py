"""Corridor arbitrage scoring (Africa market).

Different edge from crypto carry: move value across a cross-border corridor via a
stablecoin and compare the result to the official FX rate. The edge is the FX
dislocation MINUS on/off-ramp + venue fees + payout friction + **settlement-time
risk** (the FX can drift against you while the transfer settles). It is the
settlement risk + local-rail reliability — not raw spread — that decides whether a
corridor is real, which is why this is its own model and why corridors are
**alert-only** (a human settles) until licensing + payout rails exist.

Single source of truth for the corridor net-edge formula — like
shared/utils/fee_calculator for crypto, kept separate because the cost structure
(FX, ramps, settlement delay) is genuinely different.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Corridors need a bigger margin than crypto (50 bps): settlement delay + FX
# drift + local-rail friction make realized edge noisier. Conservative on purpose.
MIN_CORRIDOR_NET_EDGE_BPS = 100.0
# Below this settlement confidence we never alert, regardless of edge — the money
# is too likely to get stuck or move against us mid-transfer.
MIN_SETTLEMENT_CONFIDENCE = 0.5


@dataclass
class CorridorQuote:
    corridor: str                       # e.g. "NGN->ZAR"
    base_asset: str                     # the stablecoin bridge, e.g. "USDT"
    amount_source: float                # units of source currency to move
    stablecoin_per_source: float        # stablecoin bought per 1 source unit (buy venue, pre-fee)
    dest_per_stablecoin: float          # dest currency per 1 stablecoin (sell venue, pre-fee)
    official_fx_dest_per_source: float  # benchmark: dest per 1 source unit (direct FX)
    buy_fee_bps: float = 0.0
    sell_fee_bps: float = 0.0
    conversion_cost_bps: float = 0.0    # stablecoin on/off-ramp
    payout_friction_bps: float = 0.0    # local rail / mobile-money payout cost
    settlement_time_hours: float = 0.0
    fx_volatility_bps_per_hour: float = 0.0  # expected FX drift per hour of exposure
    venue_reliability: float = 1.0      # 0..1 local-rail/venue reliability


@dataclass
class CorridorScore:
    corridor: str
    base_asset: str
    gross_edge_bps: float
    settlement_risk_bps: float
    total_cost_bps: float
    net_edge_bps: float
    settlement_confidence: float
    viable: bool
    breakdown: dict = field(default_factory=dict)


def _settlement_confidence(venue_reliability: float, settlement_time_hours: float) -> float:
    # Reliability discounted by how long the money is in flight (decays over days).
    decay = 1.0 / (1.0 + settlement_time_hours / 24.0)
    return max(0.0, min(1.0, venue_reliability)) * decay


def score_corridor(q: CorridorQuote) -> CorridorScore:
    effective_dest = q.amount_source * q.stablecoin_per_source * q.dest_per_stablecoin
    benchmark_dest = q.amount_source * q.official_fx_dest_per_source
    gross_edge_bps = ((effective_dest / benchmark_dest) - 1.0) * 10_000.0 if benchmark_dest else 0.0

    # FX drift cost over the time the position is exposed in settlement.
    settlement_risk_bps = q.fx_volatility_bps_per_hour * q.settlement_time_hours
    total_cost_bps = (
        q.buy_fee_bps + q.sell_fee_bps + q.conversion_cost_bps
        + q.payout_friction_bps + settlement_risk_bps
    )
    net_edge_bps = gross_edge_bps - total_cost_bps
    confidence = _settlement_confidence(q.venue_reliability, q.settlement_time_hours)
    viable = net_edge_bps > MIN_CORRIDOR_NET_EDGE_BPS and confidence >= MIN_SETTLEMENT_CONFIDENCE

    return CorridorScore(
        corridor=q.corridor,
        base_asset=q.base_asset,
        gross_edge_bps=gross_edge_bps,
        settlement_risk_bps=settlement_risk_bps,
        total_cost_bps=total_cost_bps,
        net_edge_bps=net_edge_bps,
        settlement_confidence=confidence,
        viable=viable,
        breakdown={
            "buy_fee_bps": q.buy_fee_bps,
            "sell_fee_bps": q.sell_fee_bps,
            "conversion_cost_bps": q.conversion_cost_bps,
            "payout_friction_bps": q.payout_friction_bps,
            "settlement_risk_bps": settlement_risk_bps,
            "min_net_edge_bps": MIN_CORRIDOR_NET_EDGE_BPS,
        },
    )
