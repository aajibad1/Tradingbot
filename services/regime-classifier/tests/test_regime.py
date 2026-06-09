"""Regime-classifier rule tests."""
from __future__ import annotations

from regime import RegimeFeatures, classify


def test_trending():
    r = classify(RegimeFeatures(trend_strength=0.8, realized_vol_bps=20, book_depth_usd=500_000))
    assert r.regime == "trending" and r.confidence > 0.5


def test_mean_reverting():
    r = classify(RegimeFeatures(trend_strength=0.2, mean_reversion_z=3.0, book_depth_usd=500_000))
    assert r.regime == "mean_reverting"


def test_high_volatility_dominates_trend():
    r = classify(RegimeFeatures(realized_vol_bps=90, trend_strength=0.9, book_depth_usd=500_000))
    assert r.regime == "high_volatility" and r.flags["high_volatility"] is True


def test_low_liquidity_dominates_everything():
    r = classify(RegimeFeatures(book_depth_usd=10_000, realized_vol_bps=90, trend_strength=0.9))
    assert r.regime == "low_liquidity" and r.flags["low_liquidity"] is True


def test_wide_spread_is_low_liquidity():
    r = classify(RegimeFeatures(spread_bps=40, book_depth_usd=1_000_000))
    assert r.regime == "low_liquidity"


def test_ranging_default():
    r = classify(RegimeFeatures(trend_strength=0.3, mean_reversion_z=1.0, book_depth_usd=500_000))
    assert r.regime == "ranging"


def test_regime_labels_match_signal_engine_gates():
    # the gating labels signal-engine suppresses on must be producible here
    labels = {classify(RegimeFeatures(trend_strength=0.9, book_depth_usd=5e5)).regime,
              classify(RegimeFeatures(mean_reversion_z=3.0, book_depth_usd=5e5)).regime,
              classify(RegimeFeatures(book_depth_usd=1_000)).regime}
    assert {"trending", "mean_reverting", "low_liquidity"} <= labels
