"""Relational store for identity, tenancy, and RBAC.

This is the platform's transactional system-of-record (Cloud SQL Postgres in
prod) for *who the user is and what they're allowed to do* — distinct from Redis
(hot risk state) and BigQuery (analytics/event history). Tenant ids conform to
``shared.tenant.validate_tenant`` so the same id namespaces Redis keys and Secret
Manager secret ids downstream.

DATABASE_URL selects the backend; it defaults to a local SQLite file so the
service (and its tests) run with no Postgres to stand up — mirroring the repo's
"works locally without cloud deps" convention.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum_col(enum_cls):
    """A portable VARCHAR-backed enum column that stores the enum *value*
    (e.g. 'owner', not 'OWNER') and always reads back as the enum — so role /
    market / status round-trip identically whether freshly created or reloaded."""
    return SAEnum(
        enum_cls,
        native_enum=False,  # VARCHAR — same on SQLite and Postgres
        values_callable=lambda e: [m.value for m in e],
    )


class Market(str, Enum):
    AFRICA = "africa"
    GLOBAL = "global"


class OnboardingStatus(str, Enum):
    """Onboarding state machine (canonical states from the product brief).
    Transitions are enforced in onboarding.py; not every state is wired yet
    (billing/funding land with Stripe + the custody decision)."""
    ACCOUNT_CREATED = "account_created"      # signed in; tenant minted
    IDENTITY_PENDING = "identity_pending"    # region chosen; KYC in progress
    IDENTITY_VERIFIED = "identity_verified"  # KYC cleared
    BILLING_PENDING = "billing_pending"      # awaiting subscription (Stripe — later)
    BILLING_ACTIVE = "billing_active"        # subscription active (later)
    FUNDING_PENDING = "funding_pending"      # awaiting funding/key-link (later)
    REVIEW_PENDING = "review_pending"        # policy needs a human/async check
    TRADING_READY = "trading_ready"          # cleared policy; may trade (paper by default)
    RESTRICTED = "restricted"                # blocked by policy (jurisdiction/sanctions)


class KycStatus(str, Enum):
    NONE = "none"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class PlanTier(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"


class SubscriptionStatus(str, Enum):
    NONE = "none"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    ANALYST = "analyst"
    SUPPORT = "support"


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    # Matches shared.tenant.validate_tenant ([A-Za-z0-9_-]{1,64}); used as the
    # Redis/Secret-Manager namespace for this tenant's state and exchange keys.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    market: Mapped[Market | None] = mapped_column(_enum_col(Market), nullable=True)
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)  # ISO country, set at onboarding
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        _enum_col(OnboardingStatus), default=OnboardingStatus.ACCOUNT_CREATED
    )
    kyc_status: Mapped[KycStatus] = mapped_column(_enum_col(KycStatus), default=KycStatus.NONE)
    plan: Mapped[PlanTier] = mapped_column(_enum_col(PlanTier), default=PlanTier.FREE)
    subscription_status: Mapped[SubscriptionStatus] = mapped_column(
        _enum_col(SubscriptionStatus), default=SubscriptionStatus.NONE
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Version of the risk disclosures the tenant has accepted (None = none yet).
    disclosures_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    live_enabled: Mapped[bool] = mapped_column(default=False)  # paper until explicitly promoted
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")
    events: Mapped[list["OnboardingEvent"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # Firebase uid
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[Role] = mapped_column(_enum_col(Role), primary_key=True)

    user: Mapped[User] = relationship(back_populates="roles")


class OnboardingEvent(Base):
    """Append-only audit of onboarding actions/transitions. Doubles as the raw
    substrate for later analytics (region-approval times, drop-off, ML labels)."""

    __tablename__ = "onboarding_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))               # e.g. 'region.selected'
    from_status: Mapped[OnboardingStatus | None] = mapped_column(_enum_col(OnboardingStatus), nullable=True)
    to_status: Mapped[OnboardingStatus | None] = mapped_column(_enum_col(OnboardingStatus), nullable=True)
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    tenant: Mapped[Tenant] = relationship(back_populates="events")


class AuditLog(Base):
    """Append-only compliance trail of significant actions (who did what, when).
    Distinct from onboarding_events (state-machine analytics): this is the
    auditor-facing record the managed-custody model requires, surfaced behind the
    VIEW_AUDIT_LOG permission."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor: Mapped[str] = mapped_column(String(128))   # user id, or 'stripe'/'system'
    action: Mapped[str] = mapped_column(String(64))   # e.g. 'live.enabled', 'kyc.verified'
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class KycReview(Base):
    """Append-only KYC decision record — the auditable trail behind a tenant's
    kyc_status flag (who/what/when/result). Required for the regulated custody
    model; the status flag alone isn't enough for compliance review."""

    __tablename__ = "kyc_reviews"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[KycStatus] = mapped_column(_enum_col(KycStatus))
    reviewer: Mapped[str] = mapped_column(String(64))   # 'auto-stub', or a reviewer id
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class WebhookEvent(Base):
    """Idempotency ledger for inbound webhooks (Stripe now, Clerk later). The
    provider's event id is the PK, so a redelivered event is a no-op."""

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # provider event id
    source: Mapped[str] = mapped_column(String(32))                 # 'stripe' | 'clerk'
    event_type: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


_engine = None
_Session: sessionmaker | None = None


def init_db(database_url: str | None = None):
    """Create the engine + tables. Idempotent; safe to call on every startup.

    Prod runs Postgres via schema.sql migrations, but create_all is harmless
    there (it only adds missing tables) and is what makes the SQLite dev/test
    path zero-setup.
    """
    global _engine, _Session
    url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./core_api.db")
    # check_same_thread is a SQLite-only knob; FastAPI hits the session across threads.
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    _engine = create_engine(url, future=True, **kwargs)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)
    return _engine


def get_session():
    """FastAPI dependency — yields a session, always closed."""
    if _Session is None:
        init_db()
    assert _Session is not None
    db = _Session()
    try:
        yield db
    finally:
        db.close()
