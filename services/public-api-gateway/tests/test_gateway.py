"""public-api-gateway: auth + scope + metering + proxy (httpx faked by URL)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import gateway
import main


class _Resp:
    def __init__(self, status=200, json_data=None, content=None, headers=None):
        self.status_code = status
        self._json = json_data
        self.content = content if content is not None else (
            json.dumps(json_data).encode() if json_data is not None else b"")
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("POST", "http://x"), response=None)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("PARTNER_AUTH_URL", "http://partner-auth")
    monkeypatch.setenv("API_METERING_URL", "http://api-metering")
    monkeypatch.setenv("ONRAMP_URL", "http://onramp")
    state = {
        "verify": {"valid": True, "tenant_id": "ten_1", "environment": "sandbox", "scopes": ["onramp"]},
        "meter_raise": False,
        "upstream": _Resp(200, {"id": "ord_1", "status": "pending"}),
        "meter_calls": [],
        "proxy_calls": [],
    }

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/v1/partner/keys/verify"):
            return _Resp(200, state["verify"])
        if url.endswith("/v1/metering/record"):
            state["meter_calls"].append(json)
            if state["meter_raise"]:
                raise httpx.ConnectError("metering down")
            return _Resp(200, {"ok": True})
        raise AssertionError(f"unexpected POST {url}")

    def fake_request(method, url, params=None, content=None, headers=None, timeout=None):
        state["proxy_calls"].append({"method": method, "url": url, "headers": headers})
        up = state["upstream"]
        if up is None:
            raise httpx.ConnectError("upstream down")
        return up

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "request", fake_request)
    return state


@pytest.fixture()
def client(env):
    c = TestClient(main.app)
    c.state = env  # type: ignore[attr-defined]
    return c


def _auth(token="pk_sandbox_abc.secret"):
    return {"authorization": f"Bearer {token}"}


# --- auth ------------------------------------------------------------------- #


def test_missing_token_401(client):
    r = client.post("/api/onramp/v1/onramp/orders", json={})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_invalid_token_401(client):
    client.state["verify"] = {"valid": False, "reason": "revoked"}
    r = client.post("/api/onramp/v1/onramp/orders", headers=_auth(), json={})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_missing_scope_403(client):
    client.state["verify"] = {"valid": True, "tenant_id": "ten_1", "scopes": ["offramp"]}
    r = client.post("/api/onramp/v1/onramp/orders", headers=_auth(), json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


# --- routing / proxy -------------------------------------------------------- #


def test_unknown_service_404(client):
    r = client.get("/api/ghost/whatever", headers=_auth())
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_service"


def test_upstream_not_configured_502(client, monkeypatch):
    monkeypatch.delenv("ONRAMP_URL", raising=False)
    r = client.post("/api/onramp/v1/onramp/orders", headers=_auth(), json={})
    assert r.status_code == 502 and r.json()["error"]["code"] == "service_unavailable"


def test_authorized_request_is_proxied_and_metered(client):
    r = client.post("/api/onramp/v1/onramp/orders", headers=_auth(), json={"amount": 100})
    assert r.status_code == 200 and r.json()["id"] == "ord_1"
    # metered once, keyed by tenant + route
    assert client.state["meter_calls"] == [{"tenant_id": "ten_1", "route": "POST /api/onramp", "count": 1}]
    # proxied to the right upstream path, carrying tenant + correlation
    call = client.state["proxy_calls"][0]
    assert call["url"] == "http://onramp/v1/onramp/orders"
    assert call["headers"]["x-tenant-id"] == "ten_1"
    assert call["headers"]["x-correlation-id"].startswith("corr_")


def test_metering_failure_is_fail_open(client):
    client.state["meter_raise"] = True
    r = client.post("/api/onramp/v1/onramp/orders", headers=_auth(), json={"amount": 1})
    # proxy still succeeds despite metering being down
    assert r.status_code == 200 and r.json()["id"] == "ord_1"


def test_upstream_unreachable_502(client):
    client.state["upstream"] = None
    r = client.post("/api/onramp/v1/onramp/orders", headers=_auth(), json={})
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error"


# --- scope helper ----------------------------------------------------------- #


def test_has_scope_forms():
    assert gateway.has_scope(["onramp"], "onramp")
    assert gateway.has_scope(["onramp:*"], "onramp")
    assert gateway.has_scope(["*"], "anything")
    assert not gateway.has_scope(["offramp"], "onramp")
