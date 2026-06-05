"""Tests for the stage-1 validation gate — synthetic funding series, no network.

Run: PYTHONPATH=.:scripts python3 -m pytest scripts/tests/test_validation.py -v
"""
from __future__ import annotations

import backtest_funding as bt
import validate_strategy as vs


def _series(n_hours: int, rate: float, start_ms: int = 1_700_000_000_000):
    """A constant hourly funding series of length n_hours."""
    return [(start_ms + h * 3_600_000, rate) for h in range(n_hours)]


def test_run_backtest_enters_on_strong_funding():
    # 0.05%/hr ≈ +438% APR — far above the entry bar; expect multiple entries.
    fetch = lambda asset, days: _series(72 * 5 + 1, 0.0005)  # noqa: E731
    res = bt.run_backtest(["TEST"], days=20, fetch=fetch)
    assert res.trades, "strong positive funding should produce carry trades"
    assert all(t.pnl_bps > 0 for t in res.trades), "deep-in-the-money carry should be profitable"


def test_run_backtest_no_entry_on_negative_funding():
    fetch = lambda asset, days: _series(72 * 5 + 1, -0.0005)  # noqa: E731
    res = bt.run_backtest(["TEST"], days=20, fetch=fetch)
    assert res.trades == [], "negative funding is not a short-perp carry entry"


def test_max_drawdown_basic():
    # 1.0 -> 1.2 -> 0.9 : peak 1.2, trough 0.9 => dd = 0.25
    assert abs(vs.max_drawdown([1.0, 1.2, 0.9, 1.1]) - 0.25) < 1e-9


def test_sharpe_zero_variance_is_zero():
    assert vs.annualized_sharpe([0.001, 0.001, 0.001]) == 0.0


def test_sharpe_positive_for_steady_gains():
    assert vs.annualized_sharpe([0.001, 0.0012, 0.0009, 0.0011]) > 0


def test_build_daily_returns_spreads_over_hold():
    fetch = lambda asset, days: _series(72 * 3 + 1, 0.0005)  # noqa: E731
    res = bt.run_backtest(["TEST"], days=15, fetch=fetch)
    daily = vs.build_daily_returns(res.trades, n_assets=1, days=15)
    assert len(daily) == 15
    assert sum(daily) > 0, "profitable trades should yield positive cumulative daily return"
    # PnL is spread, so multiple days are non-zero (not lumped on one day).
    assert sum(1 for r in daily if r != 0) >= 3
