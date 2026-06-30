"""admin-console: UI serving + oversight proxies + best-effort health."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import main


class _Resp:
    def __init__(self, status=200, json_data=None):
        self.status_code = status
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


@pytest.fixture()
def env(monkeypatch):
    for k, v in main._DEFAULTS.items():
        monkeypatch.setenv(k, f"http://{k.lower()}")
    state = {"status_up": True}

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/status"):
            if not state["status_up"]:
                raise httpx.ConnectError("status down")
            return _Resp(200, {"status": "degraded", "components": [
                {"name": "redis", "status": "ok", "critical": True},
                {"name": "ai-ops", "status": "down", "critical": False}]})
        if url.endswith("/a2a/roster"):
            if not state["status_up"]:
                raise httpx.ConnectError("status down")
            return _Resp(200, {"status": "degraded", "count": 1,
                               "roster": ["agent-evals", "debate-service"],
                               "unreachable": ["agent-evals"],
                               "agents": [{"name": "debate-service", "version": "1.0",
                                           "description": "verify", "url": "http://debate/a2a",
                                           "skills": [{"id": "verify-claim", "name": "V",
                                                       "description": "d"}]}]})
        if url.endswith("/v1/partner/keys"):
            return _Resp(200, {"keys": [{"key_id": "pk_sandbox_x", "scopes": ["onramp"], "active": True}]})
        if url.endswith("/v1/metering/usage"):
            return _Resp(200, {"tenant_id": params["tenant"], "period": "2026-06", "total": 42, "over_quota": False})
        if url.endswith("/v1/billing/invoices"):
            return _Resp(200, {"invoices": [{"period": "2026-06", "total": 25.0, "status": "issued"}], "count": 1})
        if url.endswith("/v1/deliveries"):
            return _Resp(200, {"deliveries": [{"event_type": "payout.completed", "endpoint_id": "we_1",
                                               "status": "delivered", "response_code": 200}], "count": 1})
        return _Resp(404, {})

    monkeypatch.setattr(httpx, "get", fake_get)
    return state


@pytest.fixture()
def client(env):
    c = TestClient(main.app)
    c.state = env  # type: ignore[attr-defined]
    return c


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200 and "Admin Console" in r.text


def test_health_aggregates_status(client):
    r = client.get("/admin/health").json()
    assert r["reachable"] is True and r["report"]["status"] == "degraded"


def test_health_best_effort_when_status_down(client):
    client.state["status_up"] = False
    r = client.get("/admin/health")
    # console must still load: reachable=false, not a 502
    assert r.status_code == 200 and r.json()["reachable"] is False


def test_keys_usage_invoices_deliveries_proxy(client):
    assert client.get("/admin/keys", params={"tenant": "ten_1"}).json()["keys"][0]["key_id"] == "pk_sandbox_x"
    assert client.get("/admin/usage", params={"tenant": "ten_1"}).json()["total"] == 42
    assert client.get("/admin/invoices", params={"tenant": "ten_1"}).json()["count"] == 1
    assert client.get("/admin/deliveries").json()["deliveries"][0]["status"] == "delivered"


def test_unreachable_oversight_upstream_502(client, monkeypatch):
    def boom(url, params=None, timeout=None):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "get", boom)
    r = client.get("/admin/usage", params={"tenant": "ten_1"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_unreachable"


def test_agents_proxies_status_roster(client):
    r = client.get("/admin/agents").json()
    assert r["reachable"] is True
    m = r["roster"]
    assert m["status"] == "degraded" and m["count"] == 1
    assert m["agents"][0]["skills"][0]["id"] == "verify-claim"
    assert m["unreachable"] == ["agent-evals"]


def test_agents_best_effort_when_status_down(client):
    client.state["status_up"] = False
    r = client.get("/admin/agents")
    # console must still load: reachable=false, not a 502
    assert r.status_code == 200 and r.json()["reachable"] is False
