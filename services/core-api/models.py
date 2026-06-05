"""Wire models for core-api responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from db import KycStatus, Market, OnboardingStatus, PlanTier, Role, SubscriptionStatus


class Profile(BaseModel):
    """The authenticated user's identity, tenant, and entitlements."""

    user_id: str
    email: str | None
    tenant_id: str
    market: Market | None
    region: str | None
    onboarding_status: OnboardingStatus
    kyc_status: KycStatus
    plan: PlanTier
    subscription_status: SubscriptionStatus
    live_enabled: bool
    roles: list[Role]
    created_at: datetime


class EntitlementsView(BaseModel):
    """The tenant's plan and what it permits (necessary, not sufficient — live
    trading is still gated by KYC/policy + the validation gate + live_enabled)."""

    plan: PlanTier
    subscription_status: SubscriptionStatus
    paper_trading: bool
    live_trading: bool
    markets: list[str]
    max_live_capital_usd: float


class RegionSelectRequest(BaseModel):
    market: Market
    country: str  # ISO 3166-1 alpha-2, e.g. 'NG', 'ZA'


class KycSubmitRequest(BaseModel):
    full_name: str
    document_type: str | None = None  # placeholder until a real KYC provider is wired


class PermissionsView(BaseModel):
    roles: list[Role]
    permissions: list[str]


class OnboardingView(BaseModel):
    """Current onboarding position + what the user should do next."""

    market: Market | None
    region: str | None
    onboarding_status: OnboardingStatus
    kyc_status: KycStatus
    required_controls: list[str]
    next_actions: list[str]
