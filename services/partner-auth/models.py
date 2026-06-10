"""Wire schemas for partner-auth (docs/05 partner-auth, docs/07 api_keys)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KeyCreate(BaseModel):
    tenant_id: str = Field(description="Owning partner tenant")
    environment: str = Field(default="sandbox", description="sandbox | production")
    scopes: list[str] = Field(default_factory=list, description="Granted API scopes")
    label: str | None = Field(default=None, description="Human label for the key")


class APIKey(BaseModel):
    """Stored key metadata. ``secret_hash`` is internal; never serialized to clients
    (responses use :meth:`public`)."""

    key_id: str
    tenant_id: str
    environment: str
    scopes: list[str] = Field(default_factory=list)
    label: str | None = None
    active: bool = True
    created_at: datetime
    last_used_at: datetime | None = None
    secret_hash: str = ""

    def public(self) -> dict:
        d = self.model_dump(exclude={"secret_hash"})
        d["created_at"] = self.created_at.isoformat()
        d["last_used_at"] = self.last_used_at.isoformat() if self.last_used_at else None
        return d


class IssuedKey(BaseModel):
    """Creation/rotation response — carries the full token EXACTLY once."""

    key_id: str
    token: str = Field(description="Full credential <key_id>.<secret> — shown once, store it now")
    tenant_id: str
    environment: str
    scopes: list[str] = Field(default_factory=list)


class VerifyRequest(BaseModel):
    token: str


class VerifyResult(BaseModel):
    valid: bool
    tenant_id: str | None = None
    environment: str | None = None
    scopes: list[str] = Field(default_factory=list)
    reason: str | None = None
