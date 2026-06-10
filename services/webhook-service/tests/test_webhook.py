"""webhook-service: signing, endpoint registry, signed delivery, replay."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import main
import signing
from shared.models.event_envelope import EventEnvelope


def _env(event_type="payout.completed", event_id="evt_x", order_id="pay_1") -> dict:
    e = EventEnvelope.wrap(
        event_type=event_type,
        payload={"id": order_id, "amount": 100.0, "status": "completed"},
        producer="offramp-orchestrator", tenant_id="ten_1", event_id=event_id,
    )
    return e.model_dump(mode="json")


class _Captured(list):
    """A list that can also carry a mutable ``state`` for the fake transport."""


@pytest.fixture()
def captured(monkeypatch):
    """Capture outbound deliveries; default to a 200 response."""
    calls = _Captured()
    state = {"status": 200, "raise": False}

    def _fake_post(url, content=None, headers=None, timeout=None):
        calls.append({"url": url, "content": content, "headers": headers})
        if state["raise"]:
            raise httpx.ConnectError("refused")

        class _Resp:
            status_code = state["status"]

        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)
    calls.state = state  # type: ignore[attr-defined]
    return calls


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    main._endpoints.clear()
    main._deliveries.clear()
    main._seen_events.clear()
    return TestClient(main.app)


# --- signing ---------------------------------------------------------------- #


def test_sign_verify_roundtrip_and_tamper():
    secret = signing.new_secret()
    body = b'{"hello":"world"}'
    sig = signing.sign(secret, "1700000000", body)
    assert sig.startswith("sha256=")
    assert signing.verify(secret, "1700000000", body, sig) is True
    assert signing.verify(secret, "1700000000", body + b"x", sig) is False  # tampered body
    assert signing.verify("whsec_other", "1700000000", body, sig) is False  # wrong secret


# --- endpoint registry ------------------------------------------------------ #


def test_create_returns_secret_once_and_list_redacts(client):
    r = client.post("/v1/webhooks/endpoints", json={"url": "https://p.example/hook"})
    ep = r.json()
    assert ep["secret"] and ep["secret"].startswith("whsec_")
    listed = client.get("/v1/webhooks/endpoints").json()["endpoints"]
    assert len(listed) == 1
    assert listed[0]["secret"] is None  # never exposed on list


def test_rotate_secret_changes_it(client):
    ep = client.post("/v1/webhooks/endpoints", json={"url": "https://p.example/hook"}).json()
    rotated = client.post(f"/v1/webhooks/endpoints/{ep['id']}/rotate-secret").json()
    assert rotated["secret"] != ep["secret"]


# --- delivery --------------------------------------------------------------- #


def test_deliver_signs_payload_and_records_success(client, captured):
    ep = client.post("/v1/webhooks/endpoints", json={"url": "https://p.example/hook"}).json()
    r = client.post("/v1/events/deliver", json=_env())
    assert r.json()["matched"] == 1
    assert len(r.json()["delivered"]) == 1

    call = captured[0]
    # the delivered body is the doc-06 webhook standard
    import json as _json
    body = _json.loads(call["content"])
    assert body["type"] == "payout.completed" and body["data"]["id"] == "pay_1"
    # signature verifies with the endpoint's secret
    ts = call["headers"][signing.TIMESTAMP_HEADER]
    sig = call["headers"][signing.SIGNATURE_HEADER]
    assert signing.verify(ep["secret"], ts, call["content"], sig)
    # idempotency key is the event id
    assert call["headers"]["Idempotency-Key"] == "evt_x"


def test_event_type_filter_only_delivers_matching(client, captured):
    client.post("/v1/webhooks/endpoints",
                json={"url": "https://p.example/hook", "event_types": ["funding.completed"]})
    # a payout event does not match the funding-only endpoint
    r = client.post("/v1/events/deliver", json=_env(event_type="payout.completed"))
    assert r.json()["matched"] == 0
    assert captured == []


def test_failed_delivery_then_replay(client, captured):
    ep = client.post("/v1/webhooks/endpoints", json={"url": "https://p.example/hook"}).json()
    captured.state["raise"] = True  # type: ignore[attr-defined]
    r = client.post("/v1/events/deliver", json=_env(event_id="evt_fail"))
    assert len(r.json()["failed"]) == 1

    # recover the receiver and replay the stored event
    captured.state["raise"] = False  # type: ignore[attr-defined]
    rr = client.post(f"/v1/webhooks/endpoints/{ep['id']}/replay", json={"event_id": "evt_fail"})
    assert rr.json()["delivery"]["status"] == "delivered"


def test_replay_unknown_event_is_404(client):
    ep = client.post("/v1/webhooks/endpoints", json={"url": "https://p.example/hook"}).json()
    r = client.post(f"/v1/webhooks/endpoints/{ep['id']}/replay", json={"event_id": "nope"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "event_not_found"


def test_deliveries_log_records_attempts(client, captured):
    client.post("/v1/webhooks/endpoints", json={"url": "https://p.example/hook"})
    client.post("/v1/events/deliver", json=_env())
    log = client.get("/v1/deliveries").json()
    assert log["count"] == 1 and log["deliveries"][0]["status"] == "delivered"
