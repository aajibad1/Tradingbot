"""Tests for the net-edge calculator — the single source of truth for viability."""

import pytest

from shared.utils import fee_calculator as fc


def test_calculate_net_edge_math_and_breakdown() -> None:
    r = fc.calculate_net_edge(
        gross_spread_bps=120.0,
        long_exchange_taker_fee_bps=26.0,
        short_exchange_taker_fee_bps=7.5,
        slippage_long_bps=2.0,
        slippage_short_bps=2.0,
    )
    # total cost = 26 + 7.5 + 2 + 2 + 0 + 0 + 3 (buffer) = 40.5
    assert r["total_cost_bps"] == 40.5
    assert r["net_edge_bps"] == pytest.approx(79.5)
    assert r["is_viable"] is True  # > 50 default floor
    assert r["cost_breakdown"]["buffer"] == fc.DEFAULT_SAFETY_BUFFER_BPS


def test_funding_term_added_once() -> None:
    r = fc.calculate_net_edge(
        gross_spread_bps=0.0,
        long_exchange_taker_fee_bps=26.0,
        short_exchange_taker_fee_bps=5.0,
        slippage_long_bps=2.0,
        slippage_short_bps=2.0,
        funding_rate_bps_per_period=100.0,
    )
    # net = 0 + 100 - (26+5+2+2+3) = 62
    assert r["net_edge_bps"] == pytest.approx(62.0)
    assert r["is_viable"] is True


def test_funding_credited_over_multiple_periods() -> None:
    # Carry: 12 bps/period funding, zero gross spread, over 9 periods = 108 bps,
    # minus (26+5+2+2+3)=38 cost → 70 net. One period alone (108→12) would not clear.
    multi = fc.calculate_net_edge(
        gross_spread_bps=0.0,
        long_exchange_taker_fee_bps=26.0, short_exchange_taker_fee_bps=5.0,
        slippage_long_bps=2.0, slippage_short_bps=2.0,
        funding_rate_bps_per_period=12.0, funding_periods=9,
    )
    assert multi["net_edge_bps"] == pytest.approx(70.0)
    assert multi["is_viable"] is True

    single = fc.calculate_net_edge(
        gross_spread_bps=0.0,
        long_exchange_taker_fee_bps=26.0, short_exchange_taker_fee_bps=5.0,
        slippage_long_bps=2.0, slippage_short_bps=2.0,
        funding_rate_bps_per_period=12.0,  # funding_periods defaults to 1
    )
    assert single["net_edge_bps"] == pytest.approx(-26.0)
    assert single["is_viable"] is False


def test_threshold_override_can_tighten() -> None:
    r = fc.calculate_net_edge(
        gross_spread_bps=60.0,
        long_exchange_taker_fee_bps=0.0,
        short_exchange_taker_fee_bps=0.0,
        slippage_long_bps=0.0,
        slippage_short_bps=0.0,
        safety_buffer_bps=0.0,
        min_viable_net_edge_bps=80.0,
    )
    assert r["net_edge_bps"] == 60.0
    assert r["is_viable"] is False  # below the per-call 80 bps bar


def test_taker_fee_lookup_and_fail_loud() -> None:
    assert fc.taker_fee_bps("KRAKEN") == 26.0
    assert fc.taker_fee_bps("hyperliquid") == 5.0
    with pytest.raises(KeyError):
        fc.taker_fee_bps("nasdaq")


def test_min_viable_static_when_no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert fc.min_viable_net_edge_bps() == fc.MIN_VIABLE_NET_EDGE_BPS
