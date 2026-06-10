"""Shared HTTP API-contract layer (docs/06): correlation, error model, idempotency."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from shared.http import (
    CORRELATION_ID_HEADER,
    IDEMPOTENCY_KEY_HEADER,
    REQUEST_ID_HEADER,
    APIError,
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
    Status,
    idempotency_key,
    install_contract,
)


def _app() -> FastAPI:
    app = FastAPI()
    install_contract(app, service_name="test-svc")

    @app.get("/ok")
    def ok():
        return {"ok": True}

    @app.get("/boom-api")
    def boom_api():
        raise APIError("route_unavailable", "No route for corridor", http_status=409)

    @app.get("/boom-uncaught")
    def boom_uncaught():
        raise RuntimeError("secret internal detail")

    @app.get("/echo-key")
    def echo_key(request_key: str | None = None):
        return {"k": request_key}

    return app


# --- correlation ------------------------------------------------------------ #


def test_request_and_correlation_ids_generated_and_echoed():
    r = TestClient(_app()).get("/ok")
    assert r.status_code == 200
    assert r.headers[REQUEST_ID_HEADER].startswith("req_")
    assert r.headers[CORRELATION_ID_HEADER].startswith("corr_")


def test_inbound_ids_are_propagated_unchanged():
    r = TestClient(_app()).get(
        "/ok",
        headers={REQUEST_ID_HEADER: "req_caller", CORRELATION_ID_HEADER: "corr_action"},
    )
    assert r.headers[REQUEST_ID_HEADER] == "req_caller"
    assert r.headers[CORRELATION_ID_HEADER] == "corr_action"


# --- error model ------------------------------------------------------------ #


def test_api_error_renders_standard_envelope_with_ids():
    r = TestClient(_app()).get("/boom-api", headers={CORRELATION_ID_HEADER: "corr_xyz"})
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "route_unavailable"
    assert body["error"]["message"] == "No route for corridor"
    assert body["error"]["correlation_id"] == "corr_xyz"
    # request_id was generated and is consistent with the response header
    assert body["error"]["request_id"] == r.headers[REQUEST_ID_HEADER]


def test_uncaught_exception_is_masked_as_internal_error_but_traceable():
    client = TestClient(_app(), raise_server_exceptions=False)
    r = client.get("/boom-uncaught")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal_error"
    assert "secret internal detail" not in body["error"]["message"]  # no leak
    assert body["error"]["request_id"] is not None


# --- status taxonomy -------------------------------------------------------- #


def test_status_taxonomy_matches_doc06():
    assert Status.COMPLETED in Status.TERMINAL
    assert Status.PROCESSING not in Status.TERMINAL
    assert {"pending", "queued", "processing", "awaiting_review", "completed",
            "partially_completed", "failed", "cancelled"} == set(Status.ALL)


# --- idempotency ------------------------------------------------------------ #


def test_idempotency_key_header_read():
    app = _app()

    @app.get("/key")
    def key(request: Request):
        return {"key": idempotency_key(request)}

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/key", headers={IDEMPOTENCY_KEY_HEADER: "idem-123"})
    assert r.json().get("key") == "idem-123", (r.status_code, r.text)
    assert client.get("/key").json().get("key") is None  # absent → None


def test_in_memory_store_get_put():
    s = InMemoryIdempotencyStore()
    assert s.get("k") is None
    s.put("k", {"status": "completed", "id": "ord_1"})
    assert s.get("k") == {"status": "completed", "id": "ord_1"}


def test_redis_store_get_put_json_and_ttl():
    class _FakeRedis:
        def __init__(self):
            self.kv = {}
            self.ttls = {}

        def get(self, key):
            return self.kv.get(key)

        def set(self, key, value, ex=None):
            self.kv[key] = value
            self.ttls[key] = ex

    fake = _FakeRedis()
    s = RedisIdempotencyStore(fake)
    assert s.get("abc") is None
    s.put("abc", {"id": "ord_9"}, ttl_seconds=120)
    assert fake.kv["idem:abc"]  # namespaced
    assert fake.ttls["idem:abc"] == 120
    assert s.get("abc") == {"id": "ord_9"}
