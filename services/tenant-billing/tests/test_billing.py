"""tenant-billing: pricing logic + plan/invoice lifecycle + ledger events."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import billing
import main
from billing import Plan
from shared.pubsub.publisher import Topic


# --- pure pricing ----------------------------------------------------------- #


def test_line_items_base_only_when_under_included():
    plan = Plan("starter", base_fee=20.0, included_units=1000, per_unit_rate=0.01)
    items, total = billing.build_line_items(plan, usage_total=500)
    assert len(items) == 1 and items[0]["amount"] == 20.0
    assert total == 20.0


def test_line_items_adds_usage_over_included():
    plan = Plan("starter", base_fee=20.0, included_units=1000, per_unit_rate=0.01)
    items, total = billing.build_line_items(plan, usage_total=1500)
    assert len(items) == 2
    assert items[1]["quantity"] == 500 and items[1]["amount"] == 5.0  # 500 * 0.01
    assert total == 25.0


# --- endpoints -------------------------------------------------------------- #


class _CapturePublisher:
    def __init__(self):
        self.events = []

    def publish_event(self, topic, event_type, payload, *, producer, tenant_id=None,
                      correlation_id=None, version=1):
        self.events.append((topic, event_type, payload["id"]))
        return "msg"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("API_METERING_URL", raising=False)
    main._plans.clear()
    main._tenant_plan.clear()
    main._invoices.clear()
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "get_publisher", lambda: pub)
    c = TestClient(main.app)
    c.captured = pub  # type: ignore[attr-defined]
    return c


def _setup_plan(client, tenant="ten_1"):
    client.post("/v1/billing/plans", json={
        "plan_id": "starter", "base_fee": 20.0, "included_units": 1000, "per_unit_rate": 0.01,
    })
    client.post(f"/v1/billing/tenants/{tenant}/plan", json={"plan_id": "starter"})


def test_invoice_generation_from_usage(client):
    _setup_plan(client)
    inv = client.post("/v1/billing/invoices", json={
        "tenant_id": "ten_1", "period": "2026-06", "usage_total": 1500,
    }).json()
    assert inv["status"] == billing.DRAFT
    assert inv["total"] == 25.0
    assert inv["usage_total"] == 1500


def test_invoice_requires_assigned_plan(client):
    r = client.post("/v1/billing/invoices", json={
        "tenant_id": "ten_noplan", "period": "2026-06", "usage_total": 10,
    })
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_plan_assigned"


def test_assign_unknown_plan_404(client):
    r = client.post("/v1/billing/tenants/ten_1/plan", json={"plan_id": "ghost"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "plan_not_found"


def test_lifecycle_issue_then_pay_emits_events(client):
    _setup_plan(client)
    iid = client.post("/v1/billing/invoices", json={
        "tenant_id": "ten_1", "period": "2026-06", "usage_total": 1200,
    }).json()["id"]

    issued = client.post(f"/v1/billing/invoices/{iid}/issue").json()
    assert issued["status"] == billing.ISSUED
    paid = client.post(f"/v1/billing/invoices/{iid}/pay").json()
    assert paid["status"] == billing.PAID

    etypes = [(t, et) for (t, et, _id) in client.captured.events]
    assert etypes == [(Topic.BILLING_EVENTS, "invoice.issued"),
                      (Topic.BILLING_EVENTS, "invoice.paid")]


def test_invalid_transition_rejected(client):
    _setup_plan(client)
    iid = client.post("/v1/billing/invoices", json={
        "tenant_id": "ten_1", "period": "2026-06", "usage_total": 0,
    }).json()["id"]
    # cannot pay a draft (must issue first)
    r = client.post(f"/v1/billing/invoices/{iid}/pay")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"


def test_usage_unavailable_without_metering_url(client):
    _setup_plan(client)
    r = client.post("/v1/billing/invoices", json={"tenant_id": "ten_1", "period": "2026-06"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "usage_unavailable"
