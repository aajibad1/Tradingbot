"""account-link-service models.

Wire schemas for the exchange-onboarding flow. See docs/EXCHANGE_ONBOARDING.md.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class LinkScope(str, Enum):
    """Granted scope of a linked exchange connection. WITHDRAWAL is rejected at
    the callback — it must never reach a persisted credential."""

    READ = "read"
    TRADE = "trade"
    WITHDRAWAL = "withdrawal"  # accepted only to be explicitly REJECTED


class SessionRequest(BaseModel):
    user_id: str = Field(description="platform user_id == tenant_id")
    exchange: str = Field(description="canonical exchange id, e.g. 'binanceus'")


class SessionResponse(BaseModel):
    link_url: str = Field(description="hosted-widget URL the frontend opens")
    link_token: str = Field(description="opaque session token for correlation")


class LinkStatus(BaseModel):
    user_id: str
    exchange: str
    connected: bool = False
    scope: list[LinkScope] = Field(default_factory=list)
    last_verified: str | None = None
