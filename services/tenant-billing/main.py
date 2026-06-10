"""tenant-billing — turn metered usage into partner invoices (docs/05, docs/06).

Closes the billing loop: api-metering counts usage per tenant; this prices it
against the tenant's plan, produces an auditable invoice (base fee + per-unit
overage line items), and emits billing.* ledger events (docs/06) on invoice
issue/pay so downstream (webhooks, accounting) can react.

Usage source: the request may supply ``usage_total`` directly (tests / manual), or
omit it and let the service fetch from api-metering when API_METERING_URL is set.

Endpoints (docs/06):
  GET  /healthz
  POST /v1/billing/plans                        define a plan
  POST /v1/billing/tenants/{tenant}/plan        assign a plan to a tenant
  POST /v1/billing/invoices                      {tenant_id, period, usage_total?}
  GET  /v1/billing/invoices/{id}
  GET  /v1/billing/invoices?tenant=
  POST /v1/billing/invoices/{id}/issue          draft → issued (emits invoice.issued)
  POST /v1/billing/invoices/{id}/pay            issued → paid (emits invoice.paid)
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

import billing
from billing import Plan
from shared.http import APIError, install_contract
from shared.pubsub.publisher import Topic, get_publisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tenant-billing")

PRODUCER = "tenant-billing"

# In-memory state (single-instance sandbox; prod: Cloud SQL).
_plans: dict[str, Plan] = {}
_tenant_plan: dict[str, str] = {}
_invoices: dict[str, dict] = {}

app = FastAPI(title="tenant-billing", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _emit(event_type: str, invoice: dict) -> None:
    try:
        get_publisher().publish_event(
            Topic.BILLING_EVENTS, event_type, invoice,
            producer=PRODUCER, tenant_id=invoice.get("tenant_id"),
        )
    except Exception:  # noqa: BLE001
        logger.warning("billing event %s emit failed for invoice=%s (non-fatal)",
                       event_type, invoice.get("id"))


def _fetch_usage(tenant: str, period: str) -> int:
    """Pull a period's usage total from api-metering (when configured)."""
    url = os.environ.get("API_METERING_URL")
    if not url:
        raise APIError(
            "usage_unavailable",
            "usage_total not provided and API_METERING_URL not set",
            http_status=422,
        )
    import httpx

    r = httpx.get(f"{url.rstrip('/')}/v1/metering/usage",
                  params={"tenant": tenant, "period": period}, timeout=5.0)
    r.raise_for_status()
    return int(r.json().get("total", 0))


class PlanCreate(BaseModel):
    plan_id: str
    base_fee: float = Field(ge=0)
    included_units: int = Field(ge=0)
    per_unit_rate: float = Field(ge=0)
    currency: str = "USD"


class InvoiceRequest(BaseModel):
    tenant_id: str
    period: str = Field(description="Billing period, e.g. '2026-06'")
    usage_total: int | None = Field(default=None, ge=0)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/billing/plans")
def create_plan(req: PlanCreate) -> dict[str, Any]:
    _plans[req.plan_id] = Plan(
        plan_id=req.plan_id, base_fee=req.base_fee, included_units=req.included_units,
        per_unit_rate=req.per_unit_rate, currency=req.currency,
    )
    return {"plan_id": req.plan_id}


@app.post("/v1/billing/tenants/{tenant}/plan")
def assign_plan(tenant: str, body: dict[str, Any]) -> dict[str, Any]:
    plan_id = body.get("plan_id")
    if plan_id not in _plans:
        raise APIError("plan_not_found", f"plan {plan_id} not found", http_status=404)
    _tenant_plan[tenant] = plan_id
    return {"tenant_id": tenant, "plan_id": plan_id}


@app.post("/v1/billing/invoices")
def create_invoice(req: InvoiceRequest) -> dict[str, Any]:
    plan_id = _tenant_plan.get(req.tenant_id)
    if plan_id is None:
        raise APIError("no_plan_assigned", f"tenant {req.tenant_id} has no plan", http_status=409)
    plan = _plans[plan_id]
    usage_total = req.usage_total if req.usage_total is not None else _fetch_usage(req.tenant_id, req.period)
    line_items, total = billing.build_line_items(plan, usage_total)
    now = _now()
    invoice = {
        "id": _new_id("inv"),
        "tenant_id": req.tenant_id,
        "period": req.period,
        "plan_id": plan_id,
        "currency": plan.currency,
        "usage_total": usage_total,
        "line_items": line_items,
        "total": total,
        "status": billing.DRAFT,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    _invoices[invoice["id"]] = invoice
    return invoice


def _get_invoice(invoice_id: str) -> dict[str, Any]:
    inv = _invoices.get(invoice_id)
    if inv is None:
        raise APIError("invoice_not_found", f"invoice {invoice_id} not found", http_status=404)
    return inv


@app.get("/v1/billing/invoices/{invoice_id}")
def get_invoice(invoice_id: str) -> dict[str, Any]:
    return _get_invoice(invoice_id)


@app.get("/v1/billing/invoices")
def list_invoices(tenant: str | None = None) -> dict[str, Any]:
    out = [i for i in _invoices.values() if tenant is None or i["tenant_id"] == tenant]
    return {"invoices": out, "count": len(out)}


def _transition(invoice_id: str, frm: str, to: str, event_type: str) -> dict[str, Any]:
    inv = _get_invoice(invoice_id)
    if inv["status"] != frm:
        raise APIError(
            "invalid_transition",
            f"invoice {invoice_id} is {inv['status']!r}, cannot move to {to!r} (expected {frm!r})",
            http_status=409,
        )
    inv["status"] = to
    inv["updated_at"] = _now().isoformat()
    _invoices[invoice_id] = inv
    _emit(event_type, inv)
    return inv


@app.post("/v1/billing/invoices/{invoice_id}/issue")
def issue_invoice(invoice_id: str) -> dict[str, Any]:
    return _transition(invoice_id, billing.DRAFT, billing.ISSUED, "invoice.issued")


@app.post("/v1/billing/invoices/{invoice_id}/pay")
def pay_invoice(invoice_id: str) -> dict[str, Any]:
    return _transition(invoice_id, billing.ISSUED, billing.PAID, "invoice.paid")
