from __future__ import annotations
from anomaly import Severity, VenueSignals, detect


def test_healthy_venue():
    r = detect(VenueSignals("kraken", quote_staleness_ms=100, spread_bps=5,
                            expected_spread_bps=5, update_rate_ratio=1.0))
    assert r.status is Severity.OK and r.degraded is False and r.anomalies == []


def test_stale_quotes_is_critical():
    r = detect(VenueSignals("kraken", quote_staleness_ms=9000))
    assert r.status is Severity.CRITICAL and r.degraded
    assert any(a.kind == "stale_quotes" for a in r.anomalies)


def test_spread_jump_is_warn():
    r = detect(VenueSignals("kraken", spread_bps=30, expected_spread_bps=5, quote_staleness_ms=100))
    assert r.status is Severity.WARN
    assert any(a.kind == "spread_jump" for a in r.anomalies)


def test_sequence_gaps_critical():
    r = detect(VenueSignals("kraken", sequence_gap_rate=0.2, quote_staleness_ms=100))
    assert r.status is Severity.CRITICAL and any(a.kind == "sequence_gaps" for a in r.anomalies)


def test_update_drop_and_rejection():
    r = detect(VenueSignals("kraken", update_rate_ratio=0.2, rejection_rate=0.5, quote_staleness_ms=100))
    kinds = {a.kind for a in r.anomalies}
    assert "update_rate_drop" in kinds and "high_rejection" in kinds
    assert r.status is Severity.CRITICAL  # rejection is critical


def test_critical_dominates_warn():
    r = detect(VenueSignals("kraken", spread_bps=30, expected_spread_bps=5, sequence_gap_rate=0.2))
    assert r.status is Severity.CRITICAL


# --- connector-runtime health sink (opt-in + fail-soft) ------------------------

def _client(monkeypatch, post):
    import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main.httpx, "post", post)
    main._reported_down.clear()  # isolate the per-venue clear-tracking between tests
    return TestClient(main.app)


def _capture():
    calls = []

    def post(url, json=None, timeout=None):
        calls.append((url, json))

        class R:
            status_code = 200
        return R()

    return calls, post


def test_critical_verdict_pushed_as_down(monkeypatch):
    monkeypatch.setenv("CONNECTOR_RUNTIME_URL", "http://connectors")
    calls, post = _capture()
    client = _client(monkeypatch, post)
    r = client.post("/detect", json={"venue": "kraken", "quote_staleness_ms": 9000})
    assert r.json()["degraded"] is True
    url, body = calls[0]
    assert url == "http://connectors/v1/connectors/kraken/health"
    assert body["healthy"] is False and "stale_quotes" in body["detail"]


def test_warn_verdict_is_not_pushed(monkeypatch):
    # WARN is advisory-only — mapping a lone spread jump to catalog-DOWN would be
    # severity inflation, so nothing is reported.
    monkeypatch.setenv("CONNECTOR_RUNTIME_URL", "http://connectors")
    calls, post = _capture()
    client = _client(monkeypatch, post)
    r = client.post("/detect", json={"venue": "kraken", "spread_bps": 30,
                                     "expected_spread_bps": 5, "quote_staleness_ms": 100})
    assert r.json()["degraded"] is True and r.json()["status"] == "warn"
    assert calls == []


def test_clean_verdict_only_clears_our_own_down(monkeypatch):
    # A clean detect must NOT blindly post healthy=true (it would clobber state
    # owned by other reporters) — it clears only a DOWN this detector set.
    monkeypatch.setenv("CONNECTOR_RUNTIME_URL", "http://connectors")
    calls, post = _capture()
    client = _client(monkeypatch, post)
    clean = {"venue": "kraken", "quote_staleness_ms": 100,
             "spread_bps": 5, "expected_spread_bps": 5}
    client.post("/detect", json=clean)
    assert calls == []                                   # nothing of ours to clear
    client.post("/detect", json={"venue": "kraken", "quote_staleness_ms": 9000})
    assert calls[-1][1]["healthy"] is False              # we marked it down
    client.post("/detect", json=clean)
    assert calls[-1][1] == {"healthy": True, "detail": "ok"}  # now we clear it
    client.post("/detect", json=clean)
    assert len(calls) == 2                               # cleared once, then silent


def test_venue_with_url_metacharacters_is_rejected(monkeypatch):
    # Path-injection guard: a crafted venue must never rewrite the runtime URL
    # (e.g. into POST /v1/connectors/kraken/disable).
    monkeypatch.setenv("CONNECTOR_RUNTIME_URL", "http://connectors")

    def post(*a, **k):
        raise AssertionError("no push may happen for an invalid venue")

    client = _client(monkeypatch, post)
    for evil in ("kraken/disable?x=", "../../admin", "a b", "kraken#f", ".."):
        r = client.post("/detect", json={"venue": evil, "quote_staleness_ms": 9000})
        assert r.status_code == 422, evil
    # legitimate dotted venues still pass
    assert client.post("/detect", json={"venue": "crypto.com"}).status_code == 200


def test_no_push_when_env_unset(monkeypatch):
    monkeypatch.delenv("CONNECTOR_RUNTIME_URL", raising=False)

    def post(*a, **k):
        raise AssertionError("must not call connector-runtime when env is unset")

    client = _client(monkeypatch, post)
    assert client.post("/detect", json={"venue": "kraken"}).status_code == 200


def test_unreachable_runtime_is_fail_soft_and_keeps_state(monkeypatch):
    monkeypatch.setenv("CONNECTOR_RUNTIME_URL", "http://connectors")
    import main

    def down(*a, **k):
        raise RuntimeError("connection refused")

    client = _client(monkeypatch, down)
    r = client.post("/detect", json={"venue": "kraken", "quote_staleness_ms": 9000})
    assert r.status_code == 200 and r.json()["degraded"] is True  # /detect unaffected
    assert "kraken" not in main._reported_down  # failed push is not recorded as reported


def test_unregistered_venue_404_is_skipped_not_recorded(monkeypatch):
    monkeypatch.setenv("CONNECTOR_RUNTIME_URL", "http://connectors")
    import main

    def post(url, json=None, timeout=None):
        class R:
            status_code = 404
        return R()

    client = _client(monkeypatch, post)
    assert client.post("/detect", json={"venue": "ghost", "quote_staleness_ms": 9000}).status_code == 200
    assert "ghost" not in main._reported_down
