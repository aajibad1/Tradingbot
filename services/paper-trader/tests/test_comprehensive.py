"""Comprehensive paper-trader tests covering all requirements."""

import random
from datetime import datetime


from shared.utils.fee_calculator import EXCHANGE_TAKER_FEE_BPS
from simulator.execution_model import simulate_execution
from simulator.funding_simulator import funding_period_hours, simulate_funding_payment
from simulator.spread_collapse import sample_early_exit


# Test 1: Fee values match EXCHANGE_TAKER_FEE_BPS exactly
def test_fees_match_source_of_truth() -> None:
    """Execution model must use exact fee values from shared.utils.fee_calculator."""
    exchanges_to_test = ["coinbase", "kraken", "crypto.com", "binance.us", "hyperliquid"]

    for exchange in exchanges_to_test:
        result = simulate_execution(
            exchange=exchange,
            asset="BTC",
            side="buy",
            size_usd=10_000,
            reference_price=60_000.0,
            book_depth_usd=500_000.0,
            now=datetime(2026, 1, 1),
            rng=random.Random(42),
        )
        expected_fee_bps = EXCHANGE_TAKER_FEE_BPS[exchange]
        expected_fee_usd = 10_000 * result.fill_ratio * expected_fee_bps / 10_000.0
        assert abs(result.leg.fee_usd - expected_fee_usd) < 0.01, f"{exchange} fee mismatch"


# Test 2: Hyperliquid 1h funding vs CEX 8h
def test_funding_period_hyperliquid_vs_cex() -> None:
    """Hyperliquid funds every 1h, others every 8h."""
    assert funding_period_hours("hyperliquid") == 1.0
    assert funding_period_hours("kraken") == 8.0
    assert funding_period_hours("crypto.com") == 8.0
    assert funding_period_hours("binance.us") == 8.0
    # Unknown exchanges default to 8h
    assert funding_period_hours("unknown_exchange") == 8.0


# Test 3: Hyperliquid accrues more periods than CEX over same time
def test_hyperliquid_accrual_faster() -> None:
    """Over 24h, Hyperliquid accrues 24 periods vs 3 for CEX."""
    notional = 10_000
    rate_per_period = 0.01  # 0.01% per period

    # Hyperliquid: 24 hours / 1h = 24 periods
    hl_periods = int(24 / funding_period_hours("hyperliquid"))
    hl_funding = simulate_funding_payment(notional, rate_per_period, True, hl_periods)

    # CEX: 24 hours / 8h = 3 periods
    cex_periods = int(24 / funding_period_hours("kraken"))
    cex_funding = simulate_funding_payment(notional, rate_per_period, True, cex_periods)

    assert hl_periods == 24
    assert cex_periods == 3
    assert hl_funding > cex_funding  # Same rate, but more periods


# Test 4: Partial-fill probability over many samples (~20%)
def test_partial_fill_probability() -> None:
    """Thin markets should trigger partial fills ~20% of the time."""
    rng = random.Random(123)
    samples = 1000
    thin_market_depth = 15_000  # Order size is 10K, depth is 15K (thin)

    partial_fills = 0
    for _ in range(samples):
        result = simulate_execution(
            exchange="kraken",
            asset="BTC",
            side="buy",
            size_usd=10_000,
            reference_price=60_000.0,
            book_depth_usd=thin_market_depth,
            now=datetime(2026, 1, 1),
            rng=rng,
        )
        if result.fill_ratio < 1.0:
            partial_fills += 1

    # Should be ~20% with some tolerance for randomness
    ratio = partial_fills / samples
    assert 0.15 < ratio < 0.25, f"Got {ratio:.2%}, expected ~20%"


# Test 5: No partial fills in deep markets
def test_no_partial_fills_deep_market() -> None:
    """Deep markets (depth >> order size) should not trigger partial fills."""
    rng = random.Random(456)
    samples = 100
    deep_market_depth = 1_000_000  # 100x order size

    for _ in range(samples):
        result = simulate_execution(
            exchange="kraken",
            asset="BTC",
            side="buy",
            size_usd=10_000,
            reference_price=60_000.0,
            book_depth_usd=deep_market_depth,
            now=datetime(2026, 1, 1),
            rng=rng,
        )
        assert result.fill_ratio == 1.0


# Test 6: Spread-collapse early exit occurs sometimes
def test_spread_collapse_early_exit() -> None:
    """Early exit should occur occasionally when spread collapses."""
    rng = random.Random(789)
    samples = 1000
    planned_hours = 24.0

    early_exits = 0
    for _ in range(samples):
        exit_time = sample_early_exit(planned_hours, rng=rng)
        if exit_time is not None:
            early_exits += 1
            assert 1.0 <= exit_time < planned_hours

    # With 5% hourly probability over 24h, expect ~70% to survive full horizon
    # So ~30% should exit early
    ratio = early_exits / samples
    assert 0.20 < ratio < 0.40, f"Got {ratio:.2%} early exits"


# Test 7: Spread collapse never exits if planned_hours < 1
def test_spread_collapse_short_horizon() -> None:
    """Short horizons should never trigger early exit."""
    rng = random.Random(111)
    for _ in range(100):
        exit_time = sample_early_exit(0.5, rng=rng)
        assert exit_time is None


# Test 8: Latency is within 50-150ms
def test_execution_latency_range() -> None:
    """Every execution should have 50-150ms latency."""
    rng = random.Random(222)
    for _ in range(50):
        result = simulate_execution(
            exchange="hyperliquid",
            asset="BTC",
            side="buy",
            size_usd=10_000,
            reference_price=60_000.0,
            book_depth_usd=500_000.0,
            now=datetime(2026, 1, 1),
            rng=rng,
        )
        assert 50 <= result.latency_ms <= 150


# Test 9: Funding accrual math correctness
def test_funding_accrual_math() -> None:
    """Verify funding payment calculation."""
    # Short perp with positive funding → collect
    collected = simulate_funding_payment(
        notional_usd=50_000,
        funding_rate_pct_per_period=0.02,  # 0.02% = 2 bps
        is_short_perp=True,
        periods=10,
    )
    expected = 50_000 * 0.02 / 100.0 * 10  # $10 per period × 10 = $100
    assert abs(collected - expected) < 0.01

    # Long perp with positive funding → pay
    paid = simulate_funding_payment(
        notional_usd=50_000,
        funding_rate_pct_per_period=0.02,
        is_short_perp=False,
        periods=10,
    )
    assert paid < 0
    assert abs(paid + expected) < 0.01  # Should be negative of collected


# Test 10: Ledger does NOT import BigQuery
def test_ledger_no_bigquery_import() -> None:
    """Critical invariant: paper-trader NEVER writes to BigQuery directly."""
    import pathlib

    ledger_path = pathlib.Path(__file__).parent.parent / "ledger.py"
    content = ledger_path.read_text()

    assert "google.cloud.bigquery" not in content
    assert "from google.cloud import bigquery" not in content
    assert "BigQuery" not in content or "BigQuery — this keeps" in content  # Comment OK

    # Verify it DOES publish to Pub/Sub
    assert "Topic.TRADE_FILLS" in content
    assert "publisher.publish" in content
