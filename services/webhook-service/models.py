"""Wire schemas for the webhook service (docs/06 §Webhooks)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EndpointCreate(BaseModel):
    url: str = Field(description="HTTPS URL the partner receives deliveries at")
    tenant_id: str | None = Field(default=None, description="Owning tenant")
    event_types: list[str] = Field(
        default_factory=list,
        description="Filter: only these event_types are delivered. Empty = all.",
    )


class Endpoint(BaseModel):
    id: str
    url: str
    tenant_id: str | None = None
    event_types: list[str] = Field(default_factory=list)
    active: bool = True
    created_at: datetime
    # secret is returned only at creation / rotation, never on list/get.
    secret: str | None = None

    def matches(self, event_type: str) -> bool:
        return self.active and (not self.event_types or event_type in self.event_types)

    def redacted(self) -> "Endpoint":
        return self.model_copy(update={"secret": None})


class Delivery(BaseModel):
    id: str
    endpoint_id: str
    event_id: str
    event_type: str
    status: str = Field(description="delivered | failed")
    response_code: int | None = None
    attempts: int = 0
    created_at: datetime
    updated_at: datetime
