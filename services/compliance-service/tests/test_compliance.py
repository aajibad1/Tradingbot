"""compliance-service: KYC/KYB check + screening check state machines,
both outcomes for both, and the audit-log contract (issue #22)."""

from __future__ import annotations

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
    main._kyc_checks.clear()
    main._screening_checks.clear()
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "get_publisher", lambda: pub)
    c = TestClient(main.app)
    c.captured = pub  # type: ignore[attr-defined]
    return c


def _kyc(client, subject_id="org_1", subject_type="organization"):
    return client.post("/v1/kyc/checks", json={"subject_id": subject_id, "subject_type": subject_type}).json()


def _screening(client, subject_id="org_1", subject_type="organization"):
    return client.post("/v1/screening/checks", json={"subject_id": subject_id, "subject_type": subject_type}).json()


# --- KYC/KYB ------------------------------------------------------------- #


def test_kyc_check_starts_pending(client):
    c = _kyc(client)
    assert c["id"].startswith("kyc_")
    assert c["status"] == "pending"


def test_kyc_walks_pending_in_review_approved(client):
    cid = _kyc(client)["id"]
    r1 = client.post(f"/v1/kyc/checks/{cid}/advance", json={})
    assert r1.json()["status"] == "in_review"
    r2 = client.post(f"/v1/kyc/checks/{cid}/advance", json={"outcome": "approved"})
    assert r2.json()["status"] == "approved"


def test_kyc_walks_pending_in_review_rejected(client):
    cid = _kyc(client)["id"]
    client.post(f"/v1/kyc/checks/{cid}/advance", json={})
    r = client.post(f"/v1/kyc/checks/{cid}/advance", json={"outcome": "rejected"})
    assert r.json()["status"] == "rejected"


def test_kyc_outcome_ignored_on_first_step(client):
    """A caller can't skip the review step by supplying an outcome early."""
    cid = _kyc(client)["id"]
    r = client.post(f"/v1/kyc/checks/{cid}/advance", json={"outcome": "approved"})
    assert r.json()["status"] == "in_review"


def test_kyc_terminal_check_is_idempotent(client):
    cid = _kyc(client)["id"]
    client.post(f"/v1/kyc/checks/{cid}/advance", json={})
    client.post(f"/v1/kyc/checks/{cid}/advance", json={"outcome": "rejected"})
    r = client.post(f"/v1/kyc/checks/{cid}/advance", json={"outcome": "approved"})
    assert r.json()["status"] == "rejected"  # unchanged, not overwritten


def test_kyc_invalid_outcome_rejected(client):
    cid = _kyc(client)["id"]
    client.post(f"/v1/kyc/checks/{cid}/advance", json={})
    r = client.post(f"/v1/kyc/checks/{cid}/advance", json={"outcome": "maybe"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_outcome"


def test_kyc_check_not_found_404(client):
    r = client.post("/v1/kyc/checks/kyc_nope/advance", json={})
    assert r.status_code == 404 and r.json()["error"]["code"] == "kyc_check_not_found"


def test_kyc_invalid_subject_type_rejected(client):
    r = client.post("/v1/kyc/checks", json={"subject_id": "x", "subject_type": "robot"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_subject_type"


# --- Screening (sanctions/PEP) -------------------------------------------- #


def test_screening_check_starts_pending(client):
    c = _screening(client)
    assert c["id"].startswith("scr_")
    assert c["verdict"] == "pending"


def test_screening_clear(client):
    cid = _screening(client)["id"]
    r = client.post(f"/v1/screening/checks/{cid}/advance", json={"outcome": "clear"})
    assert r.json()["verdict"] == "clear"


def test_screening_escalated(client):
    cid = _screening(client)["id"]
    r = client.post(f"/v1/screening/checks/{cid}/advance", json={"outcome": "escalated"})
    assert r.json()["verdict"] == "escalated"


def test_screening_default_outcome_is_clear(client):
    cid = _screening(client)["id"]
    r = client.post(f"/v1/screening/checks/{cid}/advance", json={})
    assert r.json()["verdict"] == "clear"


def test_screening_terminal_check_is_idempotent(client):
    cid = _screening(client)["id"]
    client.post(f"/v1/screening/checks/{cid}/advance", json={"outcome": "escalated"})
    r = client.post(f"/v1/screening/checks/{cid}/advance", json={"outcome": "clear"})
    assert r.json()["verdict"] == "escalated"  # unchanged


def test_screening_invalid_outcome_rejected(client):
    cid = _screening(client)["id"]
    r = client.post(f"/v1/screening/checks/{cid}/advance", json={"outcome": "maybe"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_outcome"


def test_screening_check_not_found_404(client):
    r = client.post("/v1/screening/checks/scr_nope/advance", json={})
    assert r.status_code == 404 and r.json()["error"]["code"] == "screening_check_not_found"


# --- blocks_execution() (the contract's "no provider execution" guarantee) --- #


def test_pending_screening_blocks_execution():
    from models import ScreeningCheck
    from datetime import datetime

    pending = ScreeningCheck(id="scr_1", subject_id="s", subject_type="individual",
                              verdict="pending", correlation_id="c",
                              created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))
    assert pending.blocks_execution() is True


def test_escalated_screening_blocks_execution():
    from models import ScreeningCheck
    from datetime import datetime

    escalated = ScreeningCheck(id="scr_1", subject_id="s", subject_type="individual",
                                verdict="escalated", correlation_id="c",
                                created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))
    assert escalated.blocks_execution() is True


def test_clear_screening_does_not_block_execution():
    from models import ScreeningCheck
    from datetime import datetime

    clear = ScreeningCheck(id="scr_1", subject_id="s", subject_type="individual",
                            verdict="clear", correlation_id="c",
                            created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))
    assert clear.blocks_execution() is False


# --- audit trail (contract acceptance criteria: "every status change produces
# an AuditLogEntry") ---------------------------------------------------------- #


def test_every_kyc_transition_is_audited(client):
    cid = _kyc(client)["id"]
    client.post(f"/v1/kyc/checks/{cid}/advance", json={})
    client.post(f"/v1/kyc/checks/{cid}/advance", json={"outcome": "approved"})
    # create + 2 advances = 3 audit events
    assert len(client.captured.events) == 3
    for topic, entry in client.captured.events:
        assert topic is Topic.AUDIT_LOG
        assert entry.source == "compliance-service"
        assert entry.resource_type == "kyc_check"
        assert entry.resource_id == cid


def test_every_screening_transition_is_audited(client):
    cid = _screening(client)["id"]
    client.post(f"/v1/screening/checks/{cid}/advance", json={"outcome": "escalated"})
    # create + 1 advance = 2 audit events
    assert len(client.captured.events) == 2
    topics_and_types = [(t, e.event_type) for t, e in client.captured.events]
    assert (Topic.AUDIT_LOG, "screening.check_created") in topics_and_types
    assert (Topic.AUDIT_LOG, "screening.escalated") in topics_and_types


def test_audit_failure_does_not_block_the_transition(client, monkeypatch):
    """Audit is fail-open: a broken publisher must not prevent the (already
    correct) status change from taking effect or being returned."""
    def _raise(*a, **k):
        raise RuntimeError("pubsub is down")
    monkeypatch.setattr(main, "get_publisher", lambda: type("P", (), {"publish": _raise})())

    cid = _kyc(client)["id"]
    r = client.post(f"/v1/kyc/checks/{cid}/advance", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "in_review"
