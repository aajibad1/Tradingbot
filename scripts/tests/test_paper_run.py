"""Tests for the stage-2 paper-run gate — synthetic funding series, no network.

paper_run imports the paper-trader simulator, so services/paper-trader must be
on the path:

Run: PYTHONPATH=.:scripts:services/paper-trader \
       python3 -m pytest scripts/tests/test_paper_run.py -v
"""
from __future__ import annotations

from random import Random

import backtest_funding as bt
import main  # paper-trader; on PYTHONPATH for these tests
import paper_run as pr
import pytest
from models import SimulateRequest


def _series(n_hours: int, rate: float, start_ms: int = 1_700_000_000_000):
    return [(start_ms + h * 3_600_000, rate) for h in range(n_hours)]


def _strong_entries(days: int = 48):
    # 0.05%/hr ≈ +438% APR — well above the bar; many non-overlapping carry entries.
    fetch = lambda asset, days: _series(days * 24 + 1, 0.0005)  # noqa: E731
    return bt.run_backtest(["TEST"], days=days, fetch=fetch).trades


class _Entry:
    """Minimal stand-in for a backtest Trade (only the fields paper_run reads)."""
    def __init__(self, realized_funding_bps: float):
        self.asset = "TEST"
        self.entry_ts_ms = 1_700_000_000_000
        self.realized_funding_bps = realized_funding_bps


def _req(opp):
    return SimulateRequest(
        opportunity=opp,
        long_reference_price=pr.REFERENCE_PRICE,
        short_reference_price=pr.REFERENCE_PRICE,
    )


def test_simulation_is_deterministic_for_a_seed():
    entries = _strong_entries()
    assert entries
    a = pr.simulate_paper_pnls(entries, seed=42)
    b = pr.simulate_paper_pnls(entries, seed=42)
    assert a == b, "same seed must reproduce identical paper PnL (CI-gateable)"


def test_seed_changes_outcomes():
    entries = _strong_entries()  # ~16 entries → all-no-early-exit is negligible
    a = pr.simulate_paper_pnls(entries, seed=1)
    b = pr.simulate_paper_pnls(entries, seed=99999)
    assert a != b, "different seeds should drive different early-exit outcomes"


def test_realized_funding_maps_back_through_simulator(monkeypatch):
    """A full-hold simulation (no early exit) should collect ~the realized
    funding we fed in — confirming the annualized-pct mapping is faithful."""
    monkeypatch.setattr(main, "sample_early_exit", lambda *a, **k: None)
    realized_bps = 200.0  # 2% over the 72h hold
    opp = pr._opportunity_for(_Entry(realized_bps), 0)
    assert opp.funding_rate_annualized_pct == pytest.approx(
        pr._annualized_pct_from_realized(realized_bps)
    )

    trade = pr.simulate_trade(_req(opp), rng=Random(7))
    expected_funding = pr.SIZE_USD * (realized_bps / 10_000.0)
    assert trade.funding_collected_usd == pytest.approx(expected_funding, rel=1e-6)


def test_early_exit_reduces_funding_collected(monkeypatch):
    """Stage-2's key friction: a position kicked out early collects less funding
    than the full-hold credit."""
    monkeypatch.setattr(main, "sample_early_exit", lambda *a, **k: 24.0)  # exit at 24h
    opp = pr._opportunity_for(_Entry(300.0), 0)
    full_funding = pr.SIZE_USD * (300.0 / 10_000.0)
    trade = pr.simulate_trade(_req(opp), rng=Random(7))
    # 24 of 72 hourly periods collected → ~1/3 of full funding, and strictly less.
    assert 0 < trade.funding_collected_usd < full_funding
