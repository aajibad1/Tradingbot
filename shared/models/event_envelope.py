"""Canonical event envelope — the platform-wide event wrapper (docs/07).

Every domain event *can* be emitted wrapped in this envelope so the platform has
one immutable, self-describing event shape across services: a stable ``event_id``
for dedup, an ``event_type`` from the doc-07 taxonomy (``opportunity.created``,
``payout.completed``, …), the emitting ``producer``, the owning ``tenant_id``, and
a ``correlation_id`` that threads a single business action across services and
into the API correlation model (docs/06).

This is **additive and opt-in**. The existing ``EventPublisher.publish`` /
``publish_nowait`` paths (raw Pydantic payload + flat attributes) are unchanged —
rewiring every topic at once would break every consumer's
``Model.model_validate_json(message.data)`` (the hazard called out in CLAUDE.md).
New event flows adopt the envelope via :meth:`EventPublisher.publish_event`;
legacy flows keep working untouched, and can migrate one topic at a time.

The inner business object lives under ``payload`` as JSON-mode dict, so the
envelope serializes cleanly and a consumer can re-validate the payload into its
concrete model: ``Opportunity.model_validate(env.payload)``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    """A short, prefixed, collision-resistant id (``evt_<hex>`` / ``corr_<hex>``)."""
    return f"{prefix}_{uuid.uuid4().hex}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class EventEnvelope(BaseModel):
    """The doc-07 event envelope. ``payload`` carries the inner domain object."""

    event_id: str = Field(default_factory=lambda: _new_id("evt"))
    event_type: str = Field(description="Dotted taxonomy type, e.g. 'opportunity.created'")
    occurred_at: datetime = Field(default_factory=_now_utc)
    producer: str = Field(description="Emitting service name, e.g. 'opportunity-engine'")
    tenant_id: str | None = Field(default=None, description="Owning tenant; None for platform-level events")
    correlation_id: str = Field(default_factory=lambda: _new_id("corr"))
    version: int = Field(default=1, ge=1, description="Envelope/schema version for this event_type")
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def wrap(
        cls,
        *,
        event_type: str,
        payload: BaseModel | dict[str, Any],
        producer: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        version: int = 1,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> "EventEnvelope":
        """Wrap a domain object (Pydantic model or dict) in an envelope.

        A Pydantic ``payload`` is dumped in JSON mode so nested datetimes/enums
        serialize the same way they would on the wire. Unset ids/timestamps fall
        back to the model defaults (fresh ``event_id`` + ``correlation_id``, now).
        """
        body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
        fields: dict[str, Any] = {
            "event_type": event_type,
            "producer": producer,
            "tenant_id": tenant_id,
            "version": version,
            "payload": body,
        }
        if correlation_id is not None:
            fields["correlation_id"] = correlation_id
        if event_id is not None:
            fields["event_id"] = event_id
        if occurred_at is not None:
            fields["occurred_at"] = occurred_at
        return cls(**fields)

    def attributes(self) -> dict[str, str]:
        """Pub/Sub message attributes for server-side filtering / cheap routing.

        All values are strings (a Pub/Sub attribute requirement). ``tenant_id`` is
        omitted when absent rather than sent as the literal string ``"None"``."""
        attrs = {
            "event_type": self.event_type,
            "producer": self.producer,
            "correlation_id": self.correlation_id,
            "version": str(self.version),
        }
        if self.tenant_id is not None:
            attrs["tenant"] = self.tenant_id
        return attrs
