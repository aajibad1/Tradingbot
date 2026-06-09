"""Signal → DIRECTIONAL opportunity connector tests."""
from __future__ import annotations

import pytest
from directional import opportunity_from_signal

from shared.models.opportunity import StrategyType


def _sig(direction="long", edge=120.0, **over):
    base = dict(symbol="BTC/USDT", family="momentum_dislocation", direction=direction,
                gross_edge_bps=edge, confidence=0.8, expiry_ms=2000)
    base.update(over)
    return base


def test_builds_directional_opportunity():
    opp = opportunity_from_signal(_sig("long"))
    assert opp.strategy is StrategyType.DIRECTIONAL
    assert opp.direction == "long"
    assert opp.asset == "BTC"
    assert opp.long_exchange == opp.short_exchange == "hyperliquid"
    # net edge is below gross (round-trip costs subtracted)
    assert opp.net_edge_bps < opp.gross_spread_bps
    assert opp.execute is False


def test_short_direction():
    assert opportunity_from_signal(_sig("short")).direction == "short"


def test_bad_direction_raises():
    with pytest.raises(ValueError):
        opportunity_from_signal(_sig(direction="sideways"))


def test_expiry_maps_to_hold_hours():
    opp = opportunity_from_signal(_sig(expiry_ms=3_600_000))  # 1h
    assert abs(opp.min_hold_hours - 1.0) < 1e-6
    # tiny/zero expiry floors at a small hold
    assert opportunity_from_signal(_sig(expiry_ms=0)).min_hold_hours == 0.05


def test_low_edge_directional_is_not_viable_but_still_built():
    opp = opportunity_from_signal(_sig(edge=20.0))   # below cost → negative-ish net
    assert opp.strategy is StrategyType.DIRECTIONAL   # built; risk-engine gates it
    assert opp.net_edge_bps < 20.0
