"""Corridor scorer tests — the Africa corridor net-edge + settlement-risk model.

Run: PYTHONPATH=.:services/corridor-engine python3 -m pytest services/corridor-engine/tests/ -v
"""
from __future__ import annotations

from scorer import (
    MIN_CORRIDOR_NET_EDGE_BPS,
    CorridorQuote,
    score_corridor,
)


def _quote(**over) -> CorridorQuote:
    # Baseline: crypto route yields 2% more dest currency than the official FX —
    # a 200 bps gross dislocation before costs.
    base = dict(
        corridor="NGN->ZAR",
        base_asset="USDT",
        amount_source=1_000_000.0,
        stablecoin_per_source=0.00061,        # USDT per NGN at buy venue
        dest_per_stablecoin=18.36,            # ZAR per USDT at sell venue
        official_fx_dest_per_source=0.010980,  # ZAR per NGN benchmark (gives ~+2%)
    )
    base.update(over)
    return CorridorQuote(**base)


def test_clean_corridor_is_viable():
    s = score_corridor(_quote(buy_fee_bps=10, sell_fee_bps=10, conversion_cost_bps=20))
    assert s.gross_edge_bps > 150
    assert s.net_edge_bps > MIN_CORRIDOR_NET_EDGE_BPS
    assert s.viable is True


def test_costs_can_eat_the_edge():
    # Heavy ramp + payout friction pushes net below the bar.
    s = score_corridor(_quote(buy_fee_bps=40, sell_fee_bps=40, conversion_cost_bps=80,
                              payout_friction_bps=60))
    assert s.net_edge_bps < MIN_CORRIDOR_NET_EDGE_BPS
    assert s.viable is False


def test_settlement_risk_reduces_net_edge():
    fast = score_corridor(_quote(settlement_time_hours=1, fx_volatility_bps_per_hour=5))
    slow = score_corridor(_quote(settlement_time_hours=20, fx_volatility_bps_per_hour=5))
    assert slow.settlement_risk_bps > fast.settlement_risk_bps
    assert slow.net_edge_bps < fast.net_edge_bps


def test_low_settlement_confidence_blocks_even_with_edge():
    # Big edge, but a long settlement window + flaky rail → confidence below floor.
    s = score_corridor(_quote(settlement_time_hours=72, venue_reliability=0.4))
    assert s.settlement_confidence < 0.5
    assert s.viable is False  # never alert when we can't trust settlement


def test_negative_dislocation_is_not_viable():
    # crypto route is WORSE than official FX → negative gross edge
    s = score_corridor(_quote(official_fx_dest_per_source=0.012))
    assert s.gross_edge_bps < 0
    assert s.viable is False
