"""Tests for the order-book-aware slippage model."""

from datetime import datetime

import pytest

from shared.models.exchange_tick import OrderBookLevel, OrderBookSnapshot
from shared.utils import slippage_model as sm


def _book(asks=None, bids=None) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange="kraken", symbol="BTC/USD",
        asks=[OrderBookLevel(price=p, size=s) for p, s in (asks or [])],
        bids=[OrderBookLevel(price=p, size=s) for p, s in (bids or [])],
        timestamp=datetime(2026, 1, 1),
    )


def test_estimate_size_tier_bps_curve() -> None:
    assert sm.estimate_size_tier_bps(0.0) == 0.0
    assert sm.estimate_size_tier_bps(5_000.0) == 2.0       # floor
    assert sm.estimate_size_tier_bps(10_000.0) == 2.0
    assert sm.estimate_size_tier_bps(30_000.0) == pytest.approx(3.5)   # midway 2..5
    assert sm.estimate_size_tier_bps(75_000.0) == pytest.approx(8.5)   # midway 5..12
    # Extrapolation above top anchor, capped at 25.
    assert sm.estimate_size_tier_bps(10_000_000.0) == sm._FALLBACK_BPS_CAP


def test_estimate_slippage_bps_fallback_without_book() -> None:
    assert sm.estimate_slippage_bps(0.0, "buy", None) == 0.0
    assert sm.estimate_slippage_bps(50_000.0, "buy", None) == pytest.approx(5.0)
    assert sm.estimate_slippage_bps(10_000_000.0, "buy", None) == sm._FALLBACK_BPS_CAP


def test_estimate_slippage_bps_walks_book() -> None:
    # Deep book at a single level → near-zero slippage.
    book = _book(asks=[(60_000.0, 10.0)])
    assert sm.estimate_slippage_bps(60_000.0, "buy", book) == pytest.approx(0.0)
    # Two levels: fill walks up → positive slippage.
    book2 = _book(asks=[(60_000.0, 0.1), (60_600.0, 10.0)])
    bps = sm.estimate_slippage_bps(60_000.0, "buy", book2)
    assert bps > 0.0
    # Empty side → cap.
    assert sm.estimate_slippage_bps(1_000.0, "buy", _book(asks=[])) == sm._FALLBACK_BPS_CAP
    # Too shallow → cap.
    assert sm.estimate_slippage_bps(1_000_000.0, "buy", _book(asks=[(60_000.0, 0.01)])) == sm._FALLBACK_BPS_CAP


def test_guarded_rejects_when_slippage_eats_spread() -> None:
    # No book → size-tier fallback; small spread → rejected.
    est = sm.estimate_slippage_guarded(50_000.0, "buy", gross_spread_bps=4.0, snapshot=None)
    assert est.is_viable is False
    assert est.depth_usd == 0.0
    assert "slippage" in est.rejection_reason

    # Big spread → viable.
    ok = sm.estimate_slippage_guarded(10_000.0, "buy", gross_spread_bps=100.0, snapshot=None)
    assert ok.is_viable is True and ok.rejection_reason is None

    # Non-positive size short-circuits.
    zero = sm.estimate_slippage_guarded(0.0, "buy", gross_spread_bps=100.0, snapshot=None)
    assert zero.is_viable is False and zero.slippage_bps == 0.0


def test_guarded_with_book_computes_depth() -> None:
    book = _book(asks=[(60_000.0, 5.0), (60_300.0, 5.0)])
    est = sm.estimate_slippage_guarded(60_000.0, "buy", gross_spread_bps=100.0, snapshot=book)
    assert est.depth_usd > 0.0
    assert est.is_viable is True
