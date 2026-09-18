"""payment-approval-service: segregation-of-duties state machine + audit
trail (issue #21 acceptance criteria)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from shared.pubsub.publisher import Topic


class _CapturePublisher:
    def __init__(self):
        self.events = []  # (topic, AuditLogEntry)

    def publish(self, topic, payload, attributes=None):
        self.events.append((topic, payload))
        return "msg-1"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    main._requests.clear()
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "get_publisher", lambda: pub)
    c = TestClient(main.app)
    c.captured = pub  # type: ignore[attr-defined]
    return c


def _request(client, requested_by="alice", **overrides):
    body = {
        "resource_type": "onramp_order",
        "resource_id": "ord_1",
        "requested_by": requested_by,
    }
    body.update(overrides)
    return client.post("/v1/approvals", json=body).json()


# --- creation ------------------------------------------------------------- #


def test_create_starts_pending(client):
    r = _request(client)
    assert r["id"].startswith("apr_")
    assert r["status"] == "pending"
    assert r["votes"] == []


def test_empty_string_requested_by_rejected(client):
    r = client.post("/v1/approvals", json={"resource_type": "onramp_order",
                                            "resource_id": "ord_1", "requested_by": ""})
    assert r.status_code == 422


def test_empty_string_tenant_id_rejected(client):
    r = client.post("/v1/approvals", json={"resource_type": "onramp_order",
                                            "resource_id": "ord_1", "requested_by": "alice",
                                            "tenant_id": ""})
    assert r.status_code == 422


# --- acceptance criterion: self-approval rejected -------------------------- #


def test_self_approval_is_denied(client):
    rid = _request(client, requested_by="alice")["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "alice", "decision": "approve"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "self_approval_denied"
    # the request must remain pending -- a denied attempt is not a vote
    assert client.get(f"/v1/approvals/{rid}").json()["status"] == "pending"


def test_self_approval_denied_even_if_also_in_authorized_approvers(client):
    """Segregation-of-duties is unconditional -- an authorized_approvers
    list can never make the requester a valid approver for their own request."""
    rid = _request(client, requested_by="alice", authorized_approvers=["alice", "bob"])["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "alice", "decision": "approve"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "self_approval_denied"


# --- acceptance criterion: unauthorized approver denied -------------------- #


def test_unauthorized_approver_is_denied(client):
    rid = _request(client, requested_by="alice", authorized_approvers=["bob"])["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "carol", "decision": "approve"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "unauthorized_approver"


def test_empty_authorized_approvers_means_any_non_requester(client):
    rid = _request(client, requested_by="alice", authorized_approvers=[])["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "anyone", "decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_authorized_approver_can_approve(client):
    rid = _request(client, requested_by="alice", authorized_approvers=["bob"])["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved"


# --- acceptance criterion: cannot reach terminal without a recorded approval - #


def test_request_stays_pending_until_a_valid_approval_lands(client):
    rid = _request(client)["id"]
    assert client.get(f"/v1/approvals/{rid}").json()["status"] == "pending"


def test_required_approvals_greater_than_one(client):
    rid = _request(client, requested_by="alice", required_approvals=2,
                   authorized_approvers=["bob", "carol"])["id"]
    r1 = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "approve"})
    assert r1.json()["status"] == "pending"  # only 1 of 2 required
    r2 = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "carol", "decision": "approve"})
    assert r2.json()["status"] == "approved"


def test_same_approver_voting_twice_does_not_satisfy_a_two_approver_requirement(client):
    """required_approvals counts DISTINCT approvers, not vote count."""
    rid = _request(client, requested_by="alice", required_approvals=2,
                   authorized_approvers=["bob"])["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "approve"})
    assert r.json()["status"] == "pending"
    # bob is now terminal-blocked from voting again (request is still pending,
    # not terminal, so a second vote is technically allowed by this service --
    # but it must not double-count toward satisfying required_approvals).
    r2 = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "approve"})
    assert r2.json()["status"] == "pending"


def test_rejection_is_terminal_even_with_multiple_required_approvals(client):
    rid = _request(client, requested_by="alice", required_approvals=2,
                   authorized_approvers=["bob", "carol"])["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "reject"})
    assert r.json()["status"] == "rejected"


def test_terminal_request_rejects_further_decisions(client):
    rid = _request(client)["id"]
    client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "approve"})
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "carol", "decision": "reject"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_pending"


def test_invalid_decision_value_rejected(client):
    rid = _request(client)["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "maybe"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_decision"


def test_approval_not_found_404(client):
    r = client.post("/v1/approvals/apr_nope/decide", json={"decided_by": "bob", "decision": "approve"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "approval_request_not_found"


# --- acceptance criterion: approval after quote expiry re-requires review - #


def test_decision_after_quote_expiry_is_refused_and_expires_the_request(client):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    rid = _request(client, quote_expires_at=expired)["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "approve"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "quote_expired"
    # the request itself is now terminal (expired) -- a fresh request is required
    assert client.get(f"/v1/approvals/{rid}").json()["status"] == "expired"
    r2 = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "carol", "decision": "approve"})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "not_pending"


def test_decision_before_quote_expiry_still_works(client):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    rid = _request(client, quote_expires_at=future)["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved"


# --- acceptance criterion: every decision, including denials, is audited -- #


def test_creation_is_audited(client):
    _request(client)
    assert len(client.captured.events) == 1
    topic, entry = client.captured.events[0]
    assert topic is Topic.AUDIT_LOG
    assert entry.event_type == "approval.requested"


def test_successful_approval_is_audited(client):
    rid = _request(client)["id"]
    client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "approve"})
    assert len(client.captured.events) == 2
    assert client.captured.events[1][1].event_type == "approval.approved"


def test_self_approval_denial_is_audited(client):
    rid = _request(client, requested_by="alice")["id"]
    client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "alice", "decision": "approve"})
    assert len(client.captured.events) == 2  # create + the DENIED attempt
    denial = client.captured.events[1][1]
    assert denial.event_type == "approval.decision_denied.self_approval_denied"
    assert denial.metadata["outcome_code"] == "self_approval_denied"


def test_unauthorized_approver_denial_is_audited(client):
    rid = _request(client, authorized_approvers=["bob"])["id"]
    client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "mallory", "decision": "approve"})
    assert len(client.captured.events) == 2
    assert client.captured.events[1][1].event_type == "approval.decision_denied.unauthorized_approver"


def test_audit_metadata_carries_tenant_and_target_resource(client):
    _request(client, tenant_id="ten_42")
    entry = client.captured.events[0][1]
    assert entry.metadata["tenant_id"] == "ten_42"
    assert entry.metadata["target_resource_type"] == "onramp_order"
    assert entry.metadata["target_resource_id"] == "ord_1"
    assert entry.metadata["requested_by"] == "alice"


def test_audit_failure_does_not_block_the_decision(client, monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("pubsub is down")
    monkeypatch.setattr(main, "get_publisher", lambda: type("P", (), {"publish": _raise})())
    rid = _request(client)["id"]
    r = client.post(f"/v1/approvals/{rid}/decide", json={"decided_by": "bob", "decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
