"""Movement-feature computation tests."""
from __future__ import annotations

from features import Observation, build_features


def _series(venue, prices, t0=1_000_000, step=1000, bid_size=10, ask_size=10):
    return [Observation(ts_ms=t0 + i * step, venue=venue, bid=p * 0.999, ask=p * 1.001,
                        bid_size=bid_size, ask_size=ask_size) for i, p in enumerate(prices)]


def test_empty_window_is_zeros():
    f = build_features("BTC/USDT", [], now_ms=1_000_000)
    assert f.symbol == "BTC/USDT" and f.momentum_bps == 0.0 and f.realized_vol_bps == 0.0


def test_uptrend_positive_momentum_and_trend():
    f = build_features("BTC/USDT", _series("kraken", [100, 101, 102, 103, 104]), now_ms=1_010_000)
    assert f.momentum_bps > 0
    assert f.trend_strength == 1.0       # every step up
    assert f.realized_vol_bps > 0


def test_downtrend_negative_momentum():
    f = build_features("BTC/USDT", _series("kraken", [104, 103, 102, 101]), now_ms=1_010_000)
    assert f.momentum_bps < 0 and f.trend_strength == 1.0


def test_choppy_lowers_trend_strength():
    f = build_features("BTC/USDT", _series("kraken", [100, 101, 100, 101, 100]), now_ms=1_010_000)
    assert f.trend_strength < 1.0


def test_cross_venue_lag():
    # two venues, latest mids differ → venue_lag_bps > 0
    obs = _series("kraken", [100, 100]) + _series("hyperliquid", [100, 101])
    f = build_features("BTC/USDT", obs, now_ms=1_010_000)
    assert f.venue_lag_bps > 0


def test_book_imbalance_and_depth():
    obs = [Observation(ts_ms=1000, venue="kraken", bid=99.9, ask=100.1, bid_size=30, ask_size=10)]
    f = build_features("BTC/USDT", obs, now_ms=2000)
    assert f.book_imbalance == 0.5          # (30-10)/(30+10)
    assert f.book_depth_usd > 0
    assert f.quote_staleness_ms == 1000.0


def test_output_feeds_signal_and_regime_shape():
    f = build_features("BTC/USDT", _series("kraken", [100, 102, 104]), now_ms=1_010_000)
    # the fields signal-engine + regime-classifier read all exist and are numeric
    for attr in ("momentum_bps", "realized_vol_bps", "venue_lag_bps", "book_imbalance",
                 "divergence_z", "quote_staleness_ms", "trend_strength", "book_depth_usd", "spread_bps"):
        assert isinstance(getattr(f, attr), float)
