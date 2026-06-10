"""status-service aggregation tests (probes injected — no Redis/HTTP)."""
from __future__ import annotations

import main
from shared.health import HealthStatus, aggregate, build_report, run_check


def test_run_check_ok_and_down():
    ok = run_check("a", lambda: True)
    assert ok.status is HealthStatus.OK and ok.latency_ms is not None
    bad = run_check("b", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert bad.status is HealthStatus.DOWN and "boom" in bad.detail


def test_aggregate_criticality():
    from shared.health import Component
    crit_down = [Component("x", HealthStatus.DOWN, critical=True)]
    assert aggregate(crit_down) is HealthStatus.DOWN
    noncrit_down = [Component("x", HealthStatus.DOWN, critical=False),
                    Component("y", HealthStatus.OK)]
    assert aggregate(noncrit_down) is HealthStatus.DEGRADED
    assert aggregate([Component("y", HealthStatus.OK)]) is HealthStatus.OK


def test_collect_with_injected_probes():
    comps = main.collect(probes=[("redis", lambda: True, True),
                                 ("risk-engine", lambda: True, True),
                                 ("ai-ops", lambda: False, False)])
    report = build_report(comps)
    assert report.status is HealthStatus.DEGRADED      # non-critical ai-ops down
    names = {c["name"]: c for c in report.to_dict()["components"]}
    assert names["risk-engine"]["status"] == "ok"
    assert names["ai-ops"]["critical"] is False


def test_status_endpoint_503_on_critical_down(monkeypatch):
    from fastapi.testclient import TestClient
    from shared.health import Component
    monkeypatch.setattr(main, "collect", lambda: [Component("redis", HealthStatus.DOWN, critical=True)])
    r = TestClient(main.app).get("/status")
    assert r.status_code == 503 and r.json()["status"] == "down"


def test_status_endpoint_200_when_ok(monkeypatch):
    from fastapi.testclient import TestClient
    from shared.health import Component
    monkeypatch.setattr(main, "collect", lambda: [Component("redis", HealthStatus.OK)])
    r = TestClient(main.app).get("/status")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_status_endpoint_200_when_degraded(monkeypatch):
    """Degraded = a NON-critical dep is down but the platform still serves, so it
    must NOT trip external uptime monitors — 200, not 503 (docs/SLOS.md)."""
    from fastapi.testclient import TestClient
    from shared.health import Component
    monkeypatch.setattr(main, "collect", lambda: [
        Component("redis", HealthStatus.OK, critical=True),
        Component("ai-ops", HealthStatus.DOWN, critical=False),
    ])
    r = TestClient(main.app).get("/status")
    assert r.status_code == 200 and r.json()["status"] == "degraded"


def test_api_contract_correlation_headers_present(monkeypatch):
    """The shared API contract (docs/06) is wired: every response echoes
    x-request-id / x-correlation-id."""
    from fastapi.testclient import TestClient
    from shared.http import CORRELATION_ID_HEADER, REQUEST_ID_HEADER

    r = TestClient(main.app).get("/healthz")
    assert r.headers[REQUEST_ID_HEADER].startswith("req_")
    assert r.headers[CORRELATION_ID_HEADER].startswith("corr_")


def test_targets_parsing(monkeypatch):
    monkeypatch.setenv("STATUS_TARGETS", "risk-engine=http://risk:8080, core-api=http://core:8080/")
    monkeypatch.setenv("STATUS_NONCRITICAL", "core-api")
    targets = {n: (url, crit) for n, url, crit in main._targets()}
    assert targets["risk-engine"] == ("http://risk:8080/healthz", True)
    assert targets["core-api"] == ("http://core:8080/healthz", False)


def test_slo_catalog_endpoint_lists_documented_targets():
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    body = client.get("/slo/catalog").json()
    names = {s["name"] for s in body["slos"]}
    assert "control_plane_availability" in names
    assert all("objective" in s for s in body["slos"])


def test_slo_evaluate_ok_returns_200():
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.post("/slo/evaluate", json={"observations": {
        "control_plane_availability": [1000, 1000],
    }})
    assert r.status_code == 200
    body = r.json()
    assert body["worst_severity"] == "ok"
    assert body["slos"]["control_plane_availability"]["breaching"] is False


def test_slo_evaluate_fast_burn_trips_503():
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    # 2% failure against a 99.9% objective → ~20x burn → PAGE → 503.
    r = client.post("/slo/evaluate", json={"observations": {
        "control_plane_availability": [980, 1000],
    }})
    assert r.status_code == 503
    assert r.json()["worst_severity"] == "page"


def test_slo_evaluate_ignores_malformed_and_unknown():
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.post("/slo/evaluate", json={"observations": {
        "control_plane_availability": [999, 1000],
        "not_a_slo": [1, 1],
        "malformed": [5],
    }})
    assert r.status_code == 200
    assert set(r.json()["slos"]) == {"control_plane_availability"}
