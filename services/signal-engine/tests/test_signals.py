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


# --- regime auto-classification (opt-in via REGIME_CLASSIFIER_URL, fail-soft) ---

def _client(monkeypatch, post=None):
    import main
    from fastapi.testclient import TestClient
    if post is not None:
        monkeypatch.setattr(main.httpx, "post", post)
    return TestClient(main.app)


def _trending_features(**over):
    # momentum + lag → momentum signal; divergence_z=2.5 → reversion signal too
    base = dict(symbol="BTC/USD:PERP", momentum_bps=80.0, venue_lag_bps=20.0,
                realized_vol_bps=5.0, divergence_z=2.5, quote_staleness_ms=100.0,
                trend_strength=0.9)
    base.update(over)
    return base


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_omitted_regime_is_classified_and_gates(monkeypatch):
    # Classifier says "trending" → the reversion family must be suppressed.
    monkeypatch.setenv("REGIME_CLASSIFIER_URL", "http://regime")
    sent = {}

    def post(url, json=None, timeout=None):
        sent.update(json)
        return _Resp({"regime": "trending", "confidence": 0.9})

    client = _client(monkeypatch, post)
    out = client.post("/detect", json=_trending_features()).json()
    families = [s["family"] for s in out["signals"]]
    assert "momentum_dislocation" in families
    assert "stat_arb_reversion" not in families      # gated by the classified regime
    assert out["regime"] == "trending"               # classified regime echoed back
    assert sent["mean_reversion_z"] == 2.5           # divergence_z mapped across
    assert sent["trend_strength"] == 0.9


def test_explicit_regime_skips_classifier(monkeypatch):
    monkeypatch.setenv("REGIME_CLASSIFIER_URL", "http://regime")

    def _boom(*a, **k):
        raise AssertionError("classifier must not be called when regime is explicit")

    client = _client(monkeypatch, _boom)
    out = client.post("/detect", json=_trending_features(regime="mean_reverting")).json()
    families = [s["family"] for s in out["signals"]]
    assert "momentum_dislocation" not in families    # explicit regime gates instead
    assert "stat_arb_reversion" in families


def test_classifier_unreachable_detects_ungated(monkeypatch):
    monkeypatch.setenv("REGIME_CLASSIFIER_URL", "http://regime")

    def _down(*a, **k):
        raise RuntimeError("connection refused")

    client = _client(monkeypatch, _down)
    out = client.post("/detect", json=_trending_features()).json()
    families = [s["family"] for s in out["signals"]]
    # fail-soft: both families present, exactly as with no classifier configured
    assert "momentum_dislocation" in families and "stat_arb_reversion" in families


def test_no_classifier_env_means_no_call(monkeypatch):
    monkeypatch.delenv("REGIME_CLASSIFIER_URL", raising=False)

    def _boom(*a, **k):
        raise AssertionError("must not call classifier when env is unset")

    client = _client(monkeypatch, _boom)
    assert client.post("/detect", json=_trending_features()).status_code == 200


# --- signal journal (Topic.SIGNALS; fail-open) ----------------------------------

def test_emitted_signals_are_journaled_with_ids(monkeypatch):
    import main
    from shared.pubsub.publisher import Topic
    monkeypatch.delenv("REGIME_CLASSIFIER_URL", raising=False)
    published = []

    class _Pub:
        def publish(self, topic, payload, attributes=None):
            published.append((topic, payload, attributes))
            return "id"

    monkeypatch.setattr(main, "get_publisher", lambda: _Pub())
    client = _client(monkeypatch)
    out = client.post("/detect", json=_trending_features()).json()
    assert len(out["signals"]) == 2 and len(published) == 2
    # every emitted signal carries the id its journal fact was published with
    journaled = {p.signal_id for _, p, _ in published}
    assert {s["signal_id"] for s in out["signals"]} == journaled
    topic, fact, attrs = published[0]
    assert topic == Topic.SIGNALS
    assert attrs == {"family": fact.family, "symbol": "BTC/USD:PERP"}
    assert fact.direction in ("long", "short") and fact.detected_at is not None


def test_journal_failure_never_blocks_detection(monkeypatch):
    import main
    monkeypatch.delenv("REGIME_CLASSIFIER_URL", raising=False)

    class _Broken:
        def publish(self, *a, **k):
            raise RuntimeError("pubsub down")

    monkeypatch.setattr(main, "get_publisher", lambda: _Broken())
    client = _client(monkeypatch)
    out = client.post("/detect", json=_trending_features())
    assert out.status_code == 200 and len(out.json()["signals"]) == 2  # fail-open
