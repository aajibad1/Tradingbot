"""M3 regression: partial fills must reduce PnL (hedged-notional accounting).

Previously gross PnL + funding were booked on the full requested size regardless
of fill ratio, so partial fills were systematically optimistic. PnL must now
accrue only on the hedged (smaller filled leg) notional, with the unhedged
residual charged a flattening cost.
"""

from __future__ import annotations

from datetime import datetime

import main
from models import SimulateRequest
from simulator.execution_model import ExecutionResult

from shared.models.opportunity import Opportunity, StrategyType
from shared.models.trade import TradeLeg


def _opp() -> Opportunity:
    return Opportunity(
        id="m3", strategy=StrategyType.CROSS_EXCHANGE, asset="BTC",
        long_exchange="kraken", short_exchange="crypto.com",
        gross_spread_bps=100.0, trading_fees_bps=33.5, slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=0.0, net_edge_bps=58.0, confidence_score=0.8,
        recommended_size_usd=10_000.0, min_hold_hours=1.0, detected_at=datetime(2026, 1, 1),
        execute=True, long_reference_price=60_000.0, short_reference_price=60_000.0,
    )


def _exec_factory(long_ratio: float, short_ratio: float):
    def fake_exec(exchange, asset, side, size_usd, reference_price, book_depth_usd, now=None, rng=None):
        ratio = long_ratio if side == "buy" else short_ratio
        filled = size_usd * ratio
        return ExecutionResult(
            leg=TradeLeg(
                exchange=exchange, side=side, asset=asset, size=filled / reference_price,
                fill_price=reference_price, fee_usd=filled * 5 / 10_000.0,
                slippage_usd=filled * 2 / 10_000.0, filled_at=datetime(2026, 1, 1),
            ),
            requested_size=size_usd / reference_price, fill_ratio=ratio, latency_ms=100,
        )
    return fake_exec


def _run(monkeypatch, long_ratio, short_ratio) -> float:
    monkeypatch.setattr(main, "simulate_execution", _exec_factory(long_ratio, short_ratio))
    monkeypatch.setattr(main, "sample_early_exit", lambda *a, **k: None)  # no early exit → capture 1.0
    req = SimulateRequest(opportunity=_opp(), long_reference_price=60_000.0,
                          short_reference_price=60_000.0)
    return main._simulate_internal(req).gross_pnl_usd


def test_full_fill_books_full_gross(monkeypatch) -> None:
    # both legs fill 100% → gross = 10_000 * 100bps = 100.0
    assert _run(monkeypatch, 1.0, 1.0) == 100.0


def test_partial_fill_books_only_hedged_notional(monkeypatch) -> None:
    # long fills 70%, short 100% → hedged = 7_000 → gross = 7_000 * 100bps = 70.0 (not 100)
    assert _run(monkeypatch, 0.7, 1.0) == 70.0


def test_partial_fill_net_pnl_charges_residual(monkeypatch) -> None:
    monkeypatch.setattr(main, "simulate_execution", _exec_factory(0.7, 1.0))
    monkeypatch.setattr(main, "sample_early_exit", lambda *a, **k: None)
    trade = main._simulate_internal(
        SimulateRequest(opportunity=_opp(), long_reference_price=60_000.0, short_reference_price=60_000.0)
    )
    # gross 70 on hedged 7_000; fees+slip on actual fills; residual 3_000 flattening cost applied.
    assert trade.gross_pnl_usd == 70.0
    assert trade.net_pnl_usd < trade.gross_pnl_usd
