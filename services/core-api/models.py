"""Wire models for core-api responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from db import Market, OnboardingStatus, Role


class Profile(BaseModel):
    """The authenticated user's identity, tenant, and entitlements."""

    user_id: str
    email: str | None
    tenant_id: str
    market: Market | None
    region: str | None
    onboarding_status: OnboardingStatus
    live_enabled: bool
    roles: list[Role]
    created_at: datetime
