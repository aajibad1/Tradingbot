"""signal-engine detector tests — deterministic, explainable thresholds.

Run: PYTHONPATH=.:services/signal-engine python3 -m pytest services/signal-engine/tests/ -v
"""
from __future__ import annotations

from signals import Direction, MarketFeatures, SignalFamily, detect


def test_momentum_dislocation_fires_long():
    sigs = detect(MarketFeatures("BTC/USDT", momentum_bps=60.0, venue_lag_bps=15.0, realized_vol_bps=10.0))
    fam = {s.family for s in sigs}
    assert SignalFamily.MOMENTUM_DISLOCATION in fam
    mom = next(s for s in sigs if s.family is SignalFamily.MOMENTUM_DISLOCATION)
    assert mom.direction is Direction.LONG
    assert mom.gross_edge_bps == 15.0


def test_momentum_requires_lag_and_size():
    # big move but no lagging venue → nothing to capture
    assert detect(MarketFeatures("BTC/USDT", momentum_bps=60.0, venue_lag_bps=2.0)) == []
    # lag present but move too small
    assert detect(MarketFeatures("BTC/USDT", momentum_bps=10.0, venue_lag_bps=15.0)) == []


def test_orderbook_imbalance_short_on_ask_heavy():
    sigs = detect(MarketFeatures("ETH/USDT", book_imbalance=-0.8, quote_staleness_ms=100))
    imb = next(s for s in sigs if s.family is SignalFamily.ORDERBOOK_IMBALANCE)
    assert imb.direction is Direction.SHORT
    # stale quotes suppress it
    assert detect(MarketFeatures("ETH/USDT", book_imbalance=-0.8, quote_staleness_ms=2000)) == []


def test_stat_arb_reversion_shorts_high_spread():
    sigs = detect(MarketFeatures("SOL/USDT", divergence_z=3.0))
    rev = next(s for s in sigs if s.family is SignalFamily.STAT_ARB_REVERSION)
    assert rev.direction is Direction.SHORT          # z>0 → spread high → revert down
    assert detect(MarketFeatures("SOL/USDT", divergence_z=1.0)) == []  # within band


def test_regime_gating_suppresses_incompatible_families():
    f = MarketFeatures("BTC/USDT", momentum_bps=60.0, venue_lag_bps=15.0, divergence_z=3.0)
    # both momentum + reversion would fire unrestricted
    assert {s.family for s in detect(f)} >= {SignalFamily.MOMENTUM_DISLOCATION, SignalFamily.STAT_ARB_REVERSION}
    # trending regime suppresses reversion
    trending = {s.family for s in detect(f, regime="trending")}
    assert SignalFamily.STAT_ARB_REVERSION not in trending
    # mean_reverting regime suppresses momentum
    mr = {s.family for s in detect(f, regime="mean_reverting")}
    assert SignalFamily.MOMENTUM_DISLOCATION not in mr


def test_signals_sorted_by_confidence_and_have_expiry():
    sigs = detect(MarketFeatures("BTC/USDT", momentum_bps=90.0, venue_lag_bps=20.0,
                                 book_imbalance=0.9, quote_staleness_ms=50, divergence_z=3.5))
    confs = [s.confidence for s in sigs]
    assert confs == sorted(confs, reverse=True)
    assert all(s.expiry_ms > 0 for s in sigs)
