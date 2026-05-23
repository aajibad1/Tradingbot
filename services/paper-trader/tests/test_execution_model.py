import random
from datetime import datetime

from simulator.execution_model import simulate_execution
from simulator.funding_simulator import simulate_funding_payment


def test_buy_fills_above_reference() -> None:
    rng = random.Random(42)
    result = simulate_execution(
        exchange="kraken",
        asset="BTC",
        side="buy",
        size_usd=10_000,
        reference_price=60_000.0,
        book_depth_usd=500_000.0,
        now=datetime(2026, 1, 1),
        rng=rng,
    )
    assert result.leg.fill_price > 60_000.0
    assert result.leg.fee_usd > 0
    assert 0.65 <= result.fill_ratio <= 1.0


def test_sell_fills_below_reference() -> None:
    rng = random.Random(7)
    result = simulate_execution(
        exchange="hyperliquid",
        asset="BTC",
        side="sell",
        size_usd=10_000,
        reference_price=60_000.0,
        book_depth_usd=500_000.0,
        now=datetime(2026, 1, 1),
        rng=rng,
    )
    assert result.leg.fill_price < 60_000.0


def test_short_perp_collects_positive_funding() -> None:
    pnl = simulate_funding_payment(
        notional_usd=10_000,
        funding_rate_pct_per_period=0.01,
        is_short_perp=True,
        periods=3,
    )
    assert pnl > 0
    # 0.01% * 10K = $1 per period, 3 periods = $3
    assert abs(pnl - 3.0) < 0.01


def test_long_perp_pays_positive_funding() -> None:
    pnl = simulate_funding_payment(
        notional_usd=10_000,
        funding_rate_pct_per_period=0.01,
        is_short_perp=False,
        periods=3,
    )
    assert pnl < 0
