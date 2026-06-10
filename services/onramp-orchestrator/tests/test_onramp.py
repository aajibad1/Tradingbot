"""onramp-orchestrator (sandbox): contract, idempotency, lifecycle, events, guard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from shared.http import CORRELATION_ID_HEADER, IDEMPOTENCY_KEY_HEADER, Status
from shared.pubsub.publisher import Topic


class _CapturePublisher:
    def __init__(self):
        self.events = []  # (topic, event_type, order_id, correlation_id)

    def publish_event(self, topic, event_type, payload, *, producer, tenant_id=None,
                      correlation_id=None, version=1):
        self.events.append((topic, event_type, payload.id, correlation_id))
        return "msg-1"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.setenv("ONRAMP_PROVIDER", "sandbox")
    main._orders.clear()
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "get_publisher", lambda: pub)
    c = TestClient(main.app)
    c.captured = pub  # type: ignore[attr-defined]
    return c


def _order_body(amount=160000.0):
    return {
        "source_currency": "NGN",
        "dest_asset": "USDC",
        "amount": amount,
        "destination_wallet": "0xWALLET",
    }


# --- quotes ----------------------------------------------------------------- #


def test_quote_returns_fee_rate_and_dest_amount(client):
    r = client.post("/v1/onramp/quotes", json={
        "source_currency": "NGN", "dest_asset": "USDC", "amount": 160000.0,
    })
    assert r.status_code == 200
    body = r.json()
    # 1% fee on 160,000 NGN = 1,600; net 158,400 / 1600 rate = 99.0 USDC
    assert body["fee"] == 1600.0
    assert body["rate"] == 1600.0
    assert body["dest_amount"] == 99.0


# --- orders + idempotency --------------------------------------------------- #


def test_create_order_is_pending_and_emits_funding_created(client):
    r = client.post("/v1/onramp/orders", json=_order_body())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == Status.PENDING
    assert body["id"].startswith("ord_")
    # event emitted, wrapped in the envelope path, with the response correlation id
    ev = client.captured.events
    assert len(ev) == 1
    topic, etype, oid, corr = ev[0]
    assert topic is Topic.FUNDING_EVENTS and etype == "funding.created"
    assert oid == body["id"]
    assert corr == r.headers[CORRELATION_ID_HEADER]


def test_idempotency_key_replays_same_order(client):
    h = {IDEMPOTENCY_KEY_HEADER: "idem-abc"}
    a = client.post("/v1/onramp/orders", json=_order_body(), headers=h).json()
    b = client.post("/v1/onramp/orders", json=_order_body(), headers=h).json()
    assert a["id"] == b["id"]
    # only the first create emitted an event; the replay did not
    assert len(client.captured.events) == 1


def test_distinct_keys_create_distinct_orders(client):
    a = client.post("/v1/onramp/orders", json=_order_body(),
                    headers={IDEMPOTENCY_KEY_HEADER: "k1"}).json()
    b = client.post("/v1/onramp/orders", json=_order_body(),
                    headers={IDEMPOTENCY_KEY_HEADER: "k2"}).json()
    assert a["id"] != b["id"]


# --- lifecycle -------------------------------------------------------------- #


def test_advance_walks_pending_processing_completed(client):
    oid = client.post("/v1/onramp/orders", json=_order_body()).json()["id"]
    assert client.post(f"/v1/onramp/orders/{oid}/advance").json()["status"] == Status.PROCESSING
    final = client.post(f"/v1/onramp/orders/{oid}/advance").json()
    assert final["status"] == Status.COMPLETED
    # terminal stays terminal (idempotent advance)
    assert client.post(f"/v1/onramp/orders/{oid}/advance").json()["status"] == Status.COMPLETED
    etypes = [e[1] for e in client.captured.events]
    assert etypes == ["funding.created", "funding.processing", "funding.completed"]


def test_get_missing_order_is_404_error_envelope(client):
    r = client.get("/v1/onramp/orders/ord_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "order_not_found"
    assert r.json()["error"]["request_id"] is not None


# --- sandbox guard ---------------------------------------------------------- #


def test_non_sandbox_provider_is_blocked(client, monkeypatch):
    monkeypatch.setenv("ONRAMP_PROVIDER", "flutterwave")
    r = client.post("/v1/onramp/quotes", json={
        "source_currency": "NGN", "dest_asset": "USDC", "amount": 1000.0,
    })
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_not_available"
