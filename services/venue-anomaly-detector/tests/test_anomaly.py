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
    return TestClient(main.app)


def test_degraded_verdict_pushed_to_connector_runtime(monkeypatch):
    monkeypatch.setenv("CONNECTOR_RUNTIME_URL", "http://connectors")
    calls = []

    def post(url, json=None, timeout=None):
        calls.append((url, json))
        class R: status_code = 200
        return R()

    client = _client(monkeypatch, post)
    r = client.post("/detect", json={"venue": "kraken", "quote_staleness_ms": 9000})
    assert r.json()["degraded"] is True
    url, body = calls[0]
    assert url == "http://connectors/v1/connectors/kraken/health"
    assert body["healthy"] is False and "stale_quotes" in body["detail"]


def test_healthy_verdict_clears_health(monkeypatch):
    monkeypatch.setenv("CONNECTOR_RUNTIME_URL", "http://connectors")
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(json)
        class R: status_code = 200
        return R()

    client = _client(monkeypatch, post)
    client.post("/detect", json={"venue": "kraken", "quote_staleness_ms": 100,
                                 "spread_bps": 5, "expected_spread_bps": 5})
    assert calls[0] == {"healthy": True, "detail": "ok"}  # keeps the catalog current


def test_no_push_when_env_unset(monkeypatch):
    monkeypatch.delenv("CONNECTOR_RUNTIME_URL", raising=False)

    def post(*a, **k):
        raise AssertionError("must not call connector-runtime when env is unset")

    client = _client(monkeypatch, post)
    assert client.post("/detect", json={"venue": "kraken"}).status_code == 200


def test_unreachable_runtime_is_fail_soft(monkeypatch):
    monkeypatch.setenv("CONNECTOR_RUNTIME_URL", "http://connectors")

    def post(*a, **k):
        raise RuntimeError("connection refused")

    client = _client(monkeypatch, post)
    r = client.post("/detect", json={"venue": "kraken", "quote_staleness_ms": 9000})
    assert r.status_code == 200 and r.json()["degraded"] is True  # /detect unaffected
