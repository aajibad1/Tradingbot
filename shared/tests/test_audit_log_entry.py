"""AuditLogEntry — the canonical wire shape for Topic.AUDIT_LOG.

Every producer (ai-ops-agent, approval-gate-service, ...) publishes one of
these; trade-ledger is the sole consumer and validates every message against
it (services/trade-ledger/main.py:_on_audit_log). This test locks the JSON
round-trip a real Pub/Sub message goes through: publisher-side
model_dump_json() -> consumer-side AuditLogEntry(**json.loads(...))."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from shared.models.audit_log_entry import AuditLogEntry


def test_event_id_auto_generated_when_omitted():
    """The BigQuery column is REQUIRED — a caller that forgets event_id must
    still get a valid, non-empty one rather than a silently-failing insert."""
    entry = AuditLogEntry(source="risk-engine", event_type="kill_switch_reset",
                          emitted_at=datetime(2026, 1, 1))
    assert entry.event_id
    assert entry.event_id.startswith("evt_")


def test_optional_fields_default_to_none():
    entry = AuditLogEntry(source="risk-engine", event_type="kill_switch_reset",
                          emitted_at=datetime(2026, 1, 1))
    assert entry.actor is None
    assert entry.action is None
    assert entry.resource_type is None
    assert entry.resource_id is None
    assert entry.metadata is None


def test_round_trips_through_json_like_a_real_pubsub_message():
    """publisher.publish() calls model_dump_json(); the consumer decodes with
    json.loads() then AuditLogEntry(**...) — this is that exact round trip."""
    original = AuditLogEntry(
        event_id="evt_abc123",
        source="ai-ops-agent",
        event_type="proposal.risk_limit_change",
        actor="claude@ai-ops",
        action="risk_limit_change",
        resource_type="proposal",
        resource_id="prop_xyz",
        metadata={"rule": "max_daily_loss_pct", "new_value": 1.5},
        emitted_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )

    wire = original.model_dump_json()
    decoded = AuditLogEntry(**json.loads(wire))

    assert decoded.event_id == original.event_id
    assert decoded.source == original.source
    assert decoded.event_type == original.event_type
    assert decoded.metadata == original.metadata
    assert decoded.emitted_at == original.emitted_at
