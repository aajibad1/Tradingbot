"""Billing — Stripe subscription → plan entitlements.

Two concerns:
  * the PLAN CATALOG — what each plan entitles a tenant to. Entitlements are
    *necessary, not sufficient*: a plan may permit live trading, but live is still
    gated by KYC/policy + the validation gate + live_enabled (TRADING_ASSUMPTIONS).
  * the WEBHOOK — verify Stripe's signature, then apply subscription lifecycle
    events to the tenant. Idempotency is handled by the caller via WebhookEvent.

Stripe signature verification is implemented directly (HMAC-SHA256 over
``{t}.{payload}``) so the slice is testable offline with a known secret; the
official `stripe` SDK can replace verify_signature later with no API change.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from db import PlanTier, SubscriptionStatus, Tenant


@dataclass(frozen=True)
class Entitlements:
    paper_trading: bool
    live_trading: bool
    markets: list[str] = field(default_factory=list)
    max_live_capital_usd: float = 0.0


# The catalog. FREE is paper-only; PRO unlocks both markets + a live cap.
PLAN_CATALOG: dict[PlanTier, Entitlements] = {
    PlanTier.FREE:    Entitlements(paper_trading=True, live_trading=False, markets=[]),
    PlanTier.STARTER: Entitlements(paper_trading=True, live_trading=False, markets=["global"]),
    PlanTier.PRO:     Entitlements(paper_trading=True, live_trading=True,
                                   markets=["global", "africa"], max_live_capital_usd=100_000.0),
}


def entitlements_for(plan: PlanTier) -> Entitlements:
    return PLAN_CATALOG.get(plan, PLAN_CATALOG[PlanTier.FREE])


def verify_signature(payload: bytes, sig_header: str | None, secret: str) -> bool:
    """Verify a Stripe-style ``t=<ts>,v1=<hmac>`` signature header."""
    if not sig_header or not secret:
        return False
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    t, v1 = parts.get("t"), parts.get("v1")
    if not t or not v1:
        return False
    signed = t.encode() + b"." + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


# Event types that move a subscription's state.
_ACTIVATING = {"checkout.session.completed", "customer.subscription.created",
               "customer.subscription.updated", "invoice.paid"}
_CANCELING = {"customer.subscription.deleted"}


def _tenant_for(db: Session, obj: dict) -> Tenant | None:
    """Resolve the tenant: prefer explicit metadata.tenant_id / client_reference_id
    (set when we create the checkout), else fall back to the stored customer id."""
    meta = obj.get("metadata") or {}
    tid = meta.get("tenant_id") or obj.get("client_reference_id")
    if tid:
        return db.get(Tenant, tid)
    cust = obj.get("customer")
    if cust:
        return db.execute(select(Tenant).where(Tenant.stripe_customer_id == cust)).scalar_one_or_none()
    return None


def apply_event(db: Session, event: dict) -> tuple[Tenant | None, str]:
    """Apply a (already signature-verified, deduped) Stripe event. Returns the
    affected tenant (or None) plus a short summary. Unknown/unhandled types are
    ignored. The caller publishes a fresh eligibility snapshot for the tenant."""
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    tenant = _tenant_for(db, obj)
    if tenant is None:
        return None, f"{etype}: no matching tenant — ignored"

    if obj.get("customer"):
        tenant.stripe_customer_id = obj["customer"]

    if etype in _CANCELING:
        tenant.plan = PlanTier.FREE
        tenant.subscription_status = SubscriptionStatus.CANCELED
        return tenant, f"{etype}: tenant={tenant.id} -> free/canceled"

    if etype in _ACTIVATING:
        meta = obj.get("metadata") or {}
        plan_raw = (meta.get("plan") or "").lower()
        try:
            tenant.plan = PlanTier(plan_raw) if plan_raw else tenant.plan
        except ValueError:
            pass  # unknown plan string — leave plan unchanged
        tenant.subscription_status = SubscriptionStatus.ACTIVE
        return tenant, f"{etype}: tenant={tenant.id} -> {tenant.plan.value}/active"

    return tenant, f"{etype}: unhandled"
