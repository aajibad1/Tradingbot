"""approval-gate-service: permission policy + proposal lifecycle (docs/10)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
import policy


# --- pure policy ------------------------------------------------------------ #


def test_read_actions_are_auto():
    for a in ("read", "summarize", "rank", "recommend", "analyze"):
        assert policy.classify(a) == policy.AUTO


def test_sensitive_actions_need_approval():
    for a in ("risk_limit_change", "trade_size_change", "execute_trade"):
        assert policy.classify(a) == policy.PROPOSE


def test_withdrawals_and_leverage_are_blocked():
    for a in ("withdrawal", "withdraw_funds", "leverage_increase", "transfer_funds"):
        assert policy.classify(a) == policy.BLOCKED


def test_unknown_action_fails_safe_to_propose():
    assert policy.classify("frobnicate") == policy.PROPOSE
    assert policy.classify("") == policy.PROPOSE


# --- endpoints -------------------------------------------------------------- #


class _CapturePublisher:
    def __init__(self):
        self.events = []

    def publish_event(self, topic, event_type, payload, *, producer, tenant_id=None,
                      correlation_id=None, version=1):
        self.events.append((event_type, payload["id"]))
        return "msg"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    main._proposals.clear()
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "get_publisher", lambda: pub)
    c = TestClient(main.app)
    c.captured = pub  # type: ignore[attr-defined]
    return c


def _submit(client, action_type, agent="ai-ops"):
    return client.post("/v1/proposals", json={
        "agent": agent, "action_type": action_type, "summary": f"do {action_type}",
        "model_version": "claude-x", "prompt_version": "v1", "evidence": ["src://1"],
    }).json()


def test_read_proposal_auto_approved_no_human(client):
    p = _submit(client, "summarize")
    assert p["status"] == main.S_AUTO and p["decided_by"] == "policy"
    assert p["model_version"] == "claude-x" and p["evidence"] == ["src://1"]


def test_sensitive_proposal_is_held_then_approved(client):
    p = _submit(client, "risk_limit_change")
    assert p["status"] == main.S_PENDING and p["decided_by"] is None
    decided = client.post(f"/v1/proposals/{p['id']}/decide",
                          json={"decision": "approve", "decided_by": "ada", "reason": "ok"}).json()
    assert decided["status"] == main.S_APPROVED and decided["decided_by"] == "ada"


def test_sensitive_proposal_can_be_rejected(client):
    p = _submit(client, "trade_size_change")
    d = client.post(f"/v1/proposals/{p['id']}/decide",
                    json={"decision": "reject", "decided_by": "ada"}).json()
    assert d["status"] == main.S_REJECTED


def test_blocked_proposal_cannot_be_approved(client):
    p = _submit(client, "withdrawal")
    assert p["status"] == main.S_BLOCKED
    r = client.post(f"/v1/proposals/{p['id']}/decide",
                    json={"decision": "approve", "decided_by": "ada"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "action_blocked"


def test_cannot_decide_an_auto_or_decided_proposal(client):
    p = _submit(client, "summarize")  # auto_approved
    r = client.post(f"/v1/proposals/{p['id']}/decide", json={"decision": "approve", "decided_by": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_pending"


def test_invalid_decision_rejected(client):
    p = _submit(client, "execute_trade")
    r = client.post(f"/v1/proposals/{p['id']}/decide", json={"decision": "maybe", "decided_by": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_decision"


def test_list_filters_and_audit_events(client):
    _submit(client, "summarize", agent="ranker")
    _submit(client, "withdrawal", agent="ranker")
    blocked = client.get("/v1/proposals", params={"status": main.S_BLOCKED}).json()
    assert blocked["count"] == 1
    by_agent = client.get("/v1/proposals", params={"agent": "ranker"}).json()
    assert by_agent["count"] == 2
    # every proposal emitted a creation audit event
    assert [e[0] for e in client.captured.events] == ["agent.proposal_created", "agent.proposal_created"]


def test_not_found_404(client):
    assert client.get("/v1/proposals/prop_nope").status_code == 404
