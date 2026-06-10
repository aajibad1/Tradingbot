"""Settlement projection — fold funding.* / payout.* envelopes into one record.

The settlement-status service is a read model: it consumes the on-ramp and
off-ramp lifecycle events (docs/07 funding.* / payout.*) and projects them into a
single normalized settlement record per order, so partners get one status
vocabulary across both directions and all providers (docs/05 settlement-status).

Pure functions here (no I/O) so the projection is fully unit-testable; main.py
wires them to an in-memory store + the Pub/Sub subscriber.
"""

from __future__ import annotations

from typing import Any

from shared.http import Status

# event action (after the dot) → normalized settlement status.
_ACTION_TO_STATUS = {
    "created": Status.PENDING,
    "processing": Status.PROCESSING,
    "completed": Status.COMPLETED,
    "partially_completed": Status.PARTIALLY_COMPLETED,
    "failed": Status.FAILED,
    "cancelled": Status.CANCELLED,
}

# event domain (before the dot) → settlement kind.
_DOMAIN_TO_KIND = {
    "funding": "onramp",
    "payout": "offramp",
}


def status_for(event_type: str) -> str | None:
    """Map a dotted event_type (e.g. 'payout.processing') to a Status, or None
    if it isn't a recognized settlement event."""
    _, _, action = event_type.partition(".")
    return _ACTION_TO_STATUS.get(action)


def kind_for(event_type: str) -> str | None:
    domain, _, _ = event_type.partition(".")
    return _DOMAIN_TO_KIND.get(domain)


def project(prev: dict[str, Any] | None, envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Fold one envelope into the (optional) prior settlement record.

    Returns the updated record, or None if the envelope isn't a settlement event
    or carries no order id. Out-of-order/duplicate events are tolerated: the
    status always reflects the latest event applied, and history is appended."""
    event_type = envelope.get("event_type", "")
    status = status_for(event_type)
    kind = kind_for(event_type)
    if status is None or kind is None:
        return None
    payload = envelope.get("payload") or {}
    order_id = payload.get("id")
    if not order_id:
        return None

    # Normalize the two directions onto generic source/dest labels.
    if kind == "onramp":      # fiat → stablecoin
        source = payload.get("source_currency")
        dest = payload.get("dest_asset")
    else:                     # offramp: stablecoin → fiat
        source = payload.get("source_asset")
        dest = payload.get("dest_currency")

    record = dict(prev) if prev else {
        "id": order_id,
        "kind": kind,
        "correlation_id": envelope.get("correlation_id"),
        "tenant_id": envelope.get("tenant_id"),
        "source": source,
        "dest": dest,
        "amount": payload.get("amount"),
        "dest_amount": payload.get("dest_amount"),
        "history": [],
    }
    record["status"] = status
    record["updated_at"] = envelope.get("occurred_at")
    record["history"] = [*record.get("history", []), {"status": status, "event_type": event_type,
                                                       "at": envelope.get("occurred_at")}]
    return record
