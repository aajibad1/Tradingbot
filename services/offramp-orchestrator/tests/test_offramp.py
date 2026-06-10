"""offramp-orchestrator (sandbox): contract, idempotency, lifecycle, events, guard."""

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
    monkeypatch.setenv("OFFRAMP_PROVIDER", "sandbox")
    main._orders.clear()
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "get_publisher", lambda: pub)
    c = TestClient(main.app)
    c.captured = pub  # type: ignore[attr-defined]
    return c


def _order_body(amount=100.0):
    return {
        "source_asset": "USDC",
        "dest_currency": "NGN",
        "amount": amount,
        "destination_account": "GTB-0123456789",
    }


def test_quote_returns_fee_rate_and_dest_amount(client):
    r = client.post("/v1/offramp/quotes", json={
        "source_asset": "USDC", "dest_currency": "NGN", "amount": 100.0,
    })
    assert r.status_code == 200
    body = r.json()
    # 1.5% fee on 100 USDC = 1.5; net 98.5 * 1600 = 157,600 NGN
    assert body["fee"] == 1.5
    assert body["rate"] == 1600.0
    assert body["dest_amount"] == 157600.0


def test_create_payout_is_pending_and_emits_payout_created(client):
    r = client.post("/v1/offramp/orders", json=_order_body())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == Status.PENDING
    assert body["id"].startswith("pay_")
    ev = client.captured.events
    assert len(ev) == 1
    topic, etype, oid, corr = ev[0]
    assert topic is Topic.PAYOUT_EVENTS and etype == "payout.created"
    assert oid == body["id"]
    assert corr == r.headers[CORRELATION_ID_HEADER]


def test_idempotency_key_replays_same_payout(client):
    h = {IDEMPOTENCY_KEY_HEADER: "idem-xyz"}
    a = client.post("/v1/offramp/orders", json=_order_body(), headers=h).json()
    b = client.post("/v1/offramp/orders", json=_order_body(), headers=h).json()
    assert a["id"] == b["id"]
    assert len(client.captured.events) == 1


def test_advance_walks_pending_processing_completed(client):
    oid = client.post("/v1/offramp/orders", json=_order_body()).json()["id"]
    assert client.post(f"/v1/offramp/orders/{oid}/advance").json()["status"] == Status.PROCESSING
    assert client.post(f"/v1/offramp/orders/{oid}/advance").json()["status"] == Status.COMPLETED
    assert client.post(f"/v1/offramp/orders/{oid}/advance").json()["status"] == Status.COMPLETED
    assert [e[1] for e in client.captured.events] == [
        "payout.created", "payout.processing", "payout.completed",
    ]


def test_get_missing_payout_is_404_error_envelope(client):
    r = client.get("/v1/offramp/orders/pay_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "order_not_found"


def test_non_sandbox_provider_is_blocked(client, monkeypatch):
    monkeypatch.setenv("OFFRAMP_PROVIDER", "fincra")
    r = client.post("/v1/offramp/quotes", json={
        "source_asset": "USDC", "dest_currency": "NGN", "amount": 100.0,
    })
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_not_available"
