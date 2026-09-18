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
    main._idempotency._kv.clear()  # module-level singleton — tests must not leak keys
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


# --- compliance screening gate (issue #22) ----------------------------------- #


class _ScreeningResp:
    def __init__(self, verdict, status_code=200):
        self._verdict = verdict
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return {"verdict": self._verdict}


def _order_with_screening(check_id="scr_1"):
    body = _order_body()
    body["screening_check_id"] = check_id
    return body


def test_order_without_screening_check_id_is_unaffected(client):
    """No screening_check_id -> the gate never activates, even with no
    COMPLIANCE_SERVICE_URL configured — existing behavior unchanged."""
    oid = client.post("/v1/onramp/orders", json=_order_body()).json()["id"]
    assert client.post(f"/v1/onramp/orders/{oid}/advance").json()["status"] == Status.PROCESSING


def test_clear_screening_allows_advance_straight_through(client, monkeypatch):
    monkeypatch.setenv("COMPLIANCE_SERVICE_URL", "http://compliance")
    monkeypatch.setattr(main.httpx, "get", lambda url, timeout=None: _ScreeningResp("clear"))
    oid = client.post("/v1/onramp/orders", json=_order_with_screening()).json()["id"]
    r = client.post(f"/v1/onramp/orders/{oid}/advance")
    assert r.json()["status"] == Status.PROCESSING


def test_escalated_screening_routes_to_awaiting_review_not_processing(client, monkeypatch):
    """The literal acceptance criterion: an ESCALATED result blocks provider
    submission (never reaches PROCESSING) and routes to a manual-review
    state instead."""
    monkeypatch.setenv("COMPLIANCE_SERVICE_URL", "http://compliance")
    monkeypatch.setattr(main.httpx, "get", lambda url, timeout=None: _ScreeningResp("escalated"))
    oid = client.post("/v1/onramp/orders", json=_order_with_screening()).json()["id"]
    r = client.post(f"/v1/onramp/orders/{oid}/advance")
    assert r.json()["status"] == Status.AWAITING_REVIEW
    etypes = [e[1] for e in client.captured.events]
    assert etypes == ["funding.created", "funding.awaiting_review"]


def test_empty_string_screening_check_id_is_rejected_not_silently_none(client):
    """A client that defaults an unset optional field to "" instead of
    omitting it must not silently bypass the compliance gate — "" is not
    "no screening requested", it's an invalid check id. Caught by
    independent review: the gate used a truthiness check (`if
    order.screening_check_id and ...`), so "" skipped it entirely and a
    "screened" order could complete with zero compliance verification."""
    r = client.post("/v1/onramp/orders", json=_order_with_screening(check_id=""))
    assert r.status_code == 422


def test_pending_screening_also_blocks_not_just_escalated(client, monkeypatch):
    """A still-pending (not yet resolved) screening check must block too —
    absence of a CLEAR verdict is never treated as clear."""
    monkeypatch.setenv("COMPLIANCE_SERVICE_URL", "http://compliance")
    monkeypatch.setattr(main.httpx, "get", lambda url, timeout=None: _ScreeningResp("pending"))
    oid = client.post("/v1/onramp/orders", json=_order_with_screening()).json()["id"]
    r = client.post(f"/v1/onramp/orders/{oid}/advance")
    assert r.json()["status"] == Status.AWAITING_REVIEW


def test_compliance_service_unreachable_fails_closed(client, monkeypatch):
    """No COMPLIANCE_SERVICE_URL at all + a screening_check_id present -> the
    gate can't verify anything and must block, not silently proceed."""
    monkeypatch.delenv("COMPLIANCE_SERVICE_URL", raising=False)
    oid = client.post("/v1/onramp/orders", json=_order_with_screening()).json()["id"]
    r = client.post(f"/v1/onramp/orders/{oid}/advance")
    assert r.json()["status"] == Status.AWAITING_REVIEW


def test_compliance_service_error_response_fails_closed(client, monkeypatch):
    monkeypatch.setenv("COMPLIANCE_SERVICE_URL", "http://compliance")
    monkeypatch.setattr(main.httpx, "get", lambda url, timeout=None: _ScreeningResp("clear", status_code=500))
    oid = client.post("/v1/onramp/orders", json=_order_with_screening()).json()["id"]
    r = client.post(f"/v1/onramp/orders/{oid}/advance")
    assert r.json()["status"] == Status.AWAITING_REVIEW


def test_compliance_service_network_error_fails_closed(client, monkeypatch):
    def _raise(url, timeout=None):
        raise ConnectionError("unreachable")
    monkeypatch.setenv("COMPLIANCE_SERVICE_URL", "http://compliance")
    monkeypatch.setattr(main.httpx, "get", _raise)
    oid = client.post("/v1/onramp/orders", json=_order_with_screening()).json()["id"]
    r = client.post(f"/v1/onramp/orders/{oid}/advance")
    assert r.json()["status"] == Status.AWAITING_REVIEW


def test_awaiting_review_recovers_once_screening_clears(client, monkeypatch):
    """A blocked order isn't permanently stuck — once the screening check
    resolves to clear, the next advance() call proceeds to processing."""
    monkeypatch.setenv("COMPLIANCE_SERVICE_URL", "http://compliance")
    monkeypatch.setattr(main.httpx, "get", lambda url, timeout=None: _ScreeningResp("escalated"))
    oid = client.post("/v1/onramp/orders", json=_order_with_screening()).json()["id"]
    blocked = client.post(f"/v1/onramp/orders/{oid}/advance").json()
    assert blocked["status"] == Status.AWAITING_REVIEW

    monkeypatch.setattr(main.httpx, "get", lambda url, timeout=None: _ScreeningResp("clear"))
    recovered = client.post(f"/v1/onramp/orders/{oid}/advance").json()
    assert recovered["status"] == Status.PROCESSING


def test_awaiting_review_holds_if_still_not_clear(client, monkeypatch):
    """Idempotent while blocked — no duplicate awaiting_review events, no
    accidental progression."""
    monkeypatch.setenv("COMPLIANCE_SERVICE_URL", "http://compliance")
    monkeypatch.setattr(main.httpx, "get", lambda url, timeout=None: _ScreeningResp("escalated"))
    oid = client.post("/v1/onramp/orders", json=_order_with_screening()).json()["id"]
    client.post(f"/v1/onramp/orders/{oid}/advance")
    second = client.post(f"/v1/onramp/orders/{oid}/advance").json()
    assert second["status"] == Status.AWAITING_REVIEW
    etypes = [e[1] for e in client.captured.events]
    assert etypes.count("funding.awaiting_review") == 1


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
