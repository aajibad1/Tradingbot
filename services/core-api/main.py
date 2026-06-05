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
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import onboarding
from auth import Identity, current_identity
from db import (
    KycStatus,
    Market,
    OnboardingEvent,
    OnboardingStatus,
    Role,
    Tenant,
    User,
    UserRole,
    get_session,
    init_db,
)
from models import KycSubmitRequest, OnboardingView, Profile, RegionSelectRequest
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
        roles=sorted((r.role for r in user.roles), key=lambda role: role.value),
        created_at=user.created_at,
    )


def _load_user(db: Session, uid: str) -> User | None:
    return db.get(User, uid)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/sessions", response_model=Profile)
def create_session(
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_session),
) -> Profile:
    """Map an authenticated identity to a tenant. Idempotent: a returning user
    gets their existing tenant/roles; a new user gets a fresh tenant + OWNER role.
    """
    user = _load_user(db, identity.uid)
    if user is not None:
        # Keep email fresh if the IdP now provides one.
        if identity.email and user.email != identity.email:
            user.email = identity.email
            db.commit()
        return _to_profile(user)

    tenant = Tenant(id=_new_tenant_id(), display_name=identity.email or identity.uid)
    user = User(id=identity.uid, email=identity.email, tenant=tenant)
    user.roles.append(UserRole(user_id=identity.uid, role=Role.OWNER))
    db.add(tenant)
    db.add(user)
    db.commit()
    logger.info("created tenant=%s for user=%s", tenant.id, identity.uid)
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
    db.commit()
    logger.info("tenant=%s onboarding -> %s (%s)", tenant.id,
                decision.final_status.value, decision.reason)
    return _view(tenant, decision.required_controls)
