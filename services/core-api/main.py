"""core-api — identity, tenancy, and RBAC for the platform.

The root of the onboarding sequence: a Firebase-authenticated caller is mapped
(idempotently) to a tenant + user + default role in Postgres. Tenant ids conform
to ``shared.tenant.validate_tenant`` so the same id namespaces this tenant's
Redis risk state and Secret Manager exchange keys downstream — one identity, one
namespace, end to end.

Endpoints:
  GET  /healthz
  POST /v1/sessions   (auth) — create-or-get the caller's tenant/user/role; idempotent
  GET  /v1/me         (auth) — the caller's profile, or 404 if no session yet

Local mode (FIREBASE_PROJECT_ID unset, DATABASE_URL defaulting to SQLite) runs
with no cloud deps — see auth.py and db.py.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session

from decimal import Decimal

from pydantic import BaseModel

import accounts_client
import billing
import clerk
import eligibility
import onboarding
from accounts_client import AccountsClient
from auth import Identity, current_identity
from db import (
    AuditLog,
    KycStatus,
    Market,
    OnboardingEvent,
    OnboardingStatus,
    Role,
    Tenant,
    User,
    UserRole,
    WebhookEvent,
    get_session,
    init_db,
)
from models import (
    AuditEntry,
    EntitlementsView,
    KycSubmitRequest,
    OnboardingView,
    PermissionsView,
    Profile,
    RegionSelectRequest,
)
from rbac import Permission, effective_permissions
from shared.tenant import validate_tenant

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("core-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="core-api", version="0.1.0", lifespan=lifespan)


def _new_tenant_id() -> str:
    # Alphanumeric, letter-prefixed — a valid shared.tenant fragment and a safe
    # Redis/Secret-Manager namespace. One tenant per user for now (orgs later).
    return validate_tenant("t" + uuid.uuid4().hex[:16])


def _to_profile(user: User) -> Profile:
    t = user.tenant
    return Profile(
        user_id=user.id,
        email=user.email,
        tenant_id=t.id,
        market=t.market,
        region=t.region,
        onboarding_status=t.onboarding_status,
        live_enabled=t.live_enabled,
        kyc_status=t.kyc_status,
        plan=t.plan,
        subscription_status=t.subscription_status,
        roles=sorted((r.role for r in user.roles), key=lambda role: role.value),
        created_at=user.created_at,
    )


def _load_user(db: Session, uid: str) -> User | None:
    return db.get(User, uid)


def _audit(db: Session, tenant_id: str, actor: str, action: str, detail: str = "") -> None:
    """Append a compliance audit entry. Caller commits."""
    db.add(AuditLog(tenant_id=tenant_id, actor=actor, action=action, detail=detail))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _get_or_create_user(db: Session, uid: str, email: str | None) -> tuple[User, bool]:
    """Idempotently map an identity to a tenant. Returns (user, created). A new
    user gets a fresh tenant + OWNER role; a returning user has its email refreshed.
    Caller commits + audits."""
    user = _load_user(db, uid)
    if user is not None:
        if email and user.email != email:
            user.email = email
        return user, False
    tenant = Tenant(id=_new_tenant_id(), display_name=email or uid)
    user = User(id=uid, email=email, tenant=tenant)
    user.roles.append(UserRole(user_id=uid, role=Role.OWNER))
    db.add(tenant)
    db.add(user)
    return user, True


@app.post("/v1/sessions", response_model=Profile)
def create_session(
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
) -> Profile:
    """Map an authenticated identity to a tenant. Idempotent: a returning user
    gets their existing tenant/roles; a new user gets a fresh tenant + OWNER role.
    """
    user, created = _get_or_create_user(db, identity.uid, identity.email)
    if created:
        _audit(db, user.tenant.id, identity.uid, "account.created", identity.email or "")
        logger.info("created tenant=%s for user=%s", user.tenant.id, identity.uid)
    db.commit()
    return _to_profile(user)


@app.get("/v1/me", response_model=Profile)
def me(
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
) -> Profile:
    user = _load_user(db, identity.uid)
    if user is None:
        raise HTTPException(status_code=404, detail="no session — POST /v1/sessions first")
    return _to_profile(user)


# --- onboarding ---------------------------------------------------------------

def _require_tenant(db: Session, identity: Identity) -> Tenant:
    user = _load_user(db, identity.uid)
    if user is None:
        raise HTTPException(status_code=404, detail="no session — POST /v1/sessions first")
    return user.tenant


def _transition(db: Session, tenant: Tenant, to: OnboardingStatus, action: str, detail: str = "") -> None:
    """Enforce the state machine, apply the move, and append an audit event."""
    frm = tenant.onboarding_status
    try:
        onboarding.assert_transition(frm, to)
    except onboarding.TransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    tenant.onboarding_status = to
    db.add(OnboardingEvent(tenant_id=tenant.id, action=action, from_status=frm, to_status=to, detail=detail))


_NEXT_ACTION = {
    OnboardingStatus.ACCOUNT_CREATED: ["select_region"],
    OnboardingStatus.IDENTITY_PENDING: ["submit_kyc"],
    OnboardingStatus.IDENTITY_VERIFIED: ["submit_onboarding"],
    OnboardingStatus.REVIEW_PENDING: ["await_review"],
    OnboardingStatus.TRADING_READY: ["start_paper_trading"],
    OnboardingStatus.RESTRICTED: ["contact_support"],
}


def _view(tenant: Tenant, required_controls: list[str] | None = None) -> OnboardingView:
    return OnboardingView(
        market=tenant.market,
        region=tenant.region,
        onboarding_status=tenant.onboarding_status,
        kyc_status=tenant.kyc_status,
        required_controls=required_controls or [],
        next_actions=_NEXT_ACTION.get(tenant.onboarding_status, []),
    )


@app.get("/v1/onboarding", response_model=OnboardingView)
def onboarding_status(
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
) -> OnboardingView:
    return _view(_require_tenant(db, identity))


@app.post("/v1/onboarding/region", response_model=OnboardingView)
def select_region(
    req: RegionSelectRequest,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
) -> OnboardingView:
    tenant = _require_tenant(db, identity)
    tenant.market = req.market
    tenant.region = req.country.upper()
    _transition(db, tenant, OnboardingStatus.IDENTITY_PENDING, "region.selected",
                f"{req.market.value}/{tenant.region}")
    db.commit()
    return _view(tenant)


@app.post("/v1/onboarding/kyc", response_model=OnboardingView)
def submit_kyc(
    req: KycSubmitRequest,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
) -> OnboardingView:
    tenant = _require_tenant(db, identity)
    # Stub verifier: in prod this kicks off an async KYC/sanctions provider and
    # parks at PENDING until a webhook confirms. Locally it auto-verifies so the
    # flow is exercisable end to end.
    tenant.kyc_status = KycStatus.VERIFIED
    db.add(OnboardingEvent(tenant_id=tenant.id, action="kyc.submitted",
                           from_status=tenant.onboarding_status, to_status=tenant.onboarding_status,
                           detail=f"name={req.full_name}"))
    _transition(db, tenant, OnboardingStatus.IDENTITY_VERIFIED, "kyc.verified")
    _audit(db, tenant.id, identity.uid, "kyc.verified", f"name={req.full_name}")
    db.commit()
    return _view(tenant)


@app.post("/v1/onboarding/submit", response_model=OnboardingView)
def submit_onboarding(
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
) -> OnboardingView:
    tenant = _require_tenant(db, identity)
    decision = onboarding.evaluate(tenant.market, tenant.region, tenant.kyc_status)
    _transition(db, tenant, decision.final_status, "policy.evaluated", decision.reason)
    _audit(db, tenant.id, identity.uid, f"onboarding.{decision.final_status.value}", decision.reason)
    db.commit()
    eligibility.publish_snapshot(tenant)  # control-plane → execution-plane seam
    logger.info("tenant=%s onboarding -> %s (%s)", tenant.id,
                decision.final_status.value, decision.reason)
    return _view(tenant, decision.required_controls)


# --- billing ------------------------------------------------------------------

@app.get("/v1/entitlements", response_model=EntitlementsView)
def entitlements(
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
) -> EntitlementsView:
    tenant = _require_tenant(db, identity)
    ent = billing.entitlements_for(tenant.plan)
    return EntitlementsView(
        plan=tenant.plan,
        subscription_status=tenant.subscription_status,
        paper_trading=ent.paper_trading,
        live_trading=ent.live_trading,
        markets=ent.markets,
        max_live_capital_usd=ent.max_live_capital_usd,
    )


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_session)) -> dict[str, str]:
    """Stripe subscription lifecycle → plan/entitlements. Signature-verified and
    idempotent (deduped by Stripe event id)."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="billing not configured (STRIPE_WEBHOOK_SECRET unset)")

    payload = await request.body()
    if not billing.verify_signature(payload, request.headers.get("Stripe-Signature"), secret):
        raise HTTPException(status_code=400, detail="invalid Stripe signature")

    import json
    try:
        event = json.loads(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad JSON: {e}") from e

    event_id = event.get("id")
    if not event_id:
        raise HTTPException(status_code=400, detail="event missing id")

    # Idempotency: a redelivered event is a no-op.
    if db.get(WebhookEvent, event_id) is not None:
        return {"status": "duplicate", "id": event_id}
    db.add(WebhookEvent(id=event_id, source="stripe", event_type=event.get("type", "")))

    tenant, summary = billing.apply_event(db, event)
    db.commit()
    if tenant is not None:
        eligibility.publish_snapshot(tenant)  # plan change → refresh eligibility
    logger.info("stripe webhook %s", summary)
    return {"status": "ok", "summary": summary}


@app.post("/webhooks/clerk")
async def clerk_webhook(request: Request, db: Session = Depends(get_session)) -> dict[str, str]:
    """Mirror Clerk user lifecycle into Postgres. Svix-signature-verified and
    idempotent (deduped by the svix-id delivery id)."""
    secret = os.environ.get("CLERK_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="clerk sync not configured (CLERK_WEBHOOK_SECRET unset)")

    payload = await request.body()
    if not clerk.verify_signature(payload, request.headers, secret):
        raise HTTPException(status_code=400, detail="invalid Clerk (Svix) signature")

    import json
    try:
        event = json.loads(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad JSON: {e}") from e

    delivery_id = request.headers.get("svix-id")
    if not delivery_id:
        raise HTTPException(status_code=400, detail="missing svix-id")
    if db.get(WebhookEvent, delivery_id) is not None:
        return {"status": "duplicate", "id": delivery_id}
    db.add(WebhookEvent(id=delivery_id, source="clerk", event_type=event.get("type", "")))

    etype = event.get("type", "")
    data = event.get("data") or {}
    uid = data.get("id")
    summary = f"{etype}: ignored"
    if uid and etype in clerk.USER_UPSERT:
        user, created = _get_or_create_user(db, uid, clerk.primary_email(data))
        if created:
            _audit(db, user.tenant.id, "clerk", "account.created", user.email or "")
        summary = f"{etype}: tenant={user.tenant.id} ({'created' if created else 'updated'})"
    elif uid and etype in clerk.USER_DELETE:
        user = _load_user(db, uid)
        if user is not None:
            _audit(db, user.tenant_id, "clerk", "user.deleted", uid)
            db.delete(user)
            summary = f"{etype}: deleted user={uid}"

    db.commit()
    logger.info("clerk webhook %s", summary)
    return {"status": "ok", "summary": summary}


# --- RBAC + live-trading gate -------------------------------------------------

def require_permission(perm: Permission):
    """Dependency factory — 403 unless the caller's roles grant ``perm``."""
    def _dep(identity: Identity = Depends(current_identity),
             db: Session = Depends(get_session)) -> User:
        user = _load_user(db, identity.uid)
        if user is None:
            raise HTTPException(status_code=404, detail="no session — POST /v1/sessions first")
        if perm not in effective_permissions([r.role for r in user.roles]):
            raise HTTPException(status_code=403, detail=f"missing permission {perm.value}")
        return user
    return _dep


@app.get("/v1/permissions", response_model=PermissionsView)
def permissions(
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
) -> PermissionsView:
    user = _load_user(db, identity.uid)
    if user is None:
        raise HTTPException(status_code=404, detail="no session — POST /v1/sessions first")
    roles = sorted((r.role for r in user.roles), key=lambda r: r.value)
    perms = sorted(p.value for p in effective_permissions(roles))
    return PermissionsView(roles=roles, permissions=perms)


@app.post("/v1/trading/live-enable", response_model=Profile)
def live_enable(
    user: User = Depends(require_permission(Permission.ENABLE_LIVE_TRADING)),
    db: Session = Depends(get_session),
) -> Profile:
    """Flip a tenant from paper to live-eligible — only when ALL gates pass:
      1. caller has the ENABLE_LIVE_TRADING permission (dependency above),
      2. the tenant's PLAN entitles live trading,
      3. onboarding is TRADING_READY (KYC + regional policy cleared).
    NOTE: live_enabled is the entitlement flag only; actual live execution still
    requires the per-tenant validation gate (Sharpe + paper run) per
    TRADING_ASSUMPTIONS.md. Billing/permission can't bypass that.
    """
    tenant = user.tenant
    if not billing.entitlements_for(tenant.plan).live_trading:
        raise HTTPException(status_code=403, detail=f"plan '{tenant.plan.value}' does not include live trading")
    if tenant.onboarding_status is not OnboardingStatus.TRADING_READY:
        raise HTTPException(
            status_code=409,
            detail=f"onboarding is '{tenant.onboarding_status.value}', not trading_ready",
        )
    tenant.live_enabled = True
    _audit(db, tenant.id, user.id, "live.enabled", f"plan={tenant.plan.value}")
    db.commit()
    eligibility.publish_snapshot(tenant)
    logger.info("tenant=%s live-enabled (plan=%s)", tenant.id, tenant.plan.value)
    return _to_profile(user)


# --- funding (managed custody, paper mode) ------------------------------------

class FundingRequest(BaseModel):
    asset: str
    amount: Decimal


def get_accounts_client() -> AccountsClient:
    """FastAPI dependency — the accounts-service client. Tests override this."""
    url = os.environ.get("ACCOUNTS_SERVICE_URL")
    if not url:
        raise HTTPException(status_code=503, detail="funding not configured (ACCOUNTS_SERVICE_URL unset)")
    return AccountsClient(url)


@app.get("/v1/funding/balances")
def funding_balances(
    asset: str,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
    ac: AccountsClient = Depends(get_accounts_client),
) -> dict:
    tenant = _require_tenant(db, identity)
    return ac.balances(tenant.id, asset)


@app.post("/v1/funding/deposit")
def funding_deposit(
    body: FundingRequest,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
    ac: AccountsClient = Depends(get_accounts_client),
) -> dict:
    """Fund the tenant's account (paper mode — recorded in the internal ledger)."""
    tenant = _require_tenant(db, identity)
    result = ac.deposit(tenant.id, body.asset, body.amount)
    _audit(db, tenant.id, identity.uid, "funding.deposit", f"{body.amount} {body.asset}")
    db.commit()
    return result


@app.post("/v1/funding/withdraw")
def funding_withdraw(
    body: FundingRequest,
    user: User = Depends(require_permission(Permission.REQUEST_WITHDRAWAL)),
    db: Session = Depends(get_session),
    ac: AccountsClient = Depends(get_accounts_client),
) -> dict:
    """Withdraw from the tenant's balance. Owner-only (REQUEST_WITHDRAWAL)."""
    try:
        result = ac.withdraw(user.tenant_id, body.asset, body.amount)
    except accounts_client.AccountsInsufficientFunds as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    _audit(db, user.tenant_id, user.id, "funding.withdraw", f"{body.amount} {body.asset}")
    db.commit()
    return result


@app.get("/v1/audit", response_model=list[AuditEntry])
def audit_log(
    limit: int = 50,
    user: User = Depends(require_permission(Permission.VIEW_AUDIT_LOG)),
    db: Session = Depends(get_session),
) -> list[AuditEntry]:
    """The tenant's compliance audit trail, newest first. Requires VIEW_AUDIT_LOG."""
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.tenant_id == user.tenant_id)
        .order_by(AuditLog.id.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    return [
        AuditEntry(actor=r.actor, action=r.action, detail=r.detail, created_at=r.created_at)
        for r in rows
    ]
