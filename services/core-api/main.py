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

from auth import Identity, current_identity
from db import OnboardingStatus, Role, Tenant, User, UserRole, get_session, init_db
from models import Profile
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
