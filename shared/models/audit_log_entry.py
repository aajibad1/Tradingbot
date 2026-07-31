from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class AuditLogEntry(BaseModel):
    """A row in the system-wide audit log (``arb_audit.audit_log`` — see
    ``services/trade-ledger/schema/audit_log.sql``), the tamper-evident record
    docs/10 requires (output traceability, human-override audit trail).

    Every service that emits to ``Topic.AUDIT_LOG`` publishes one of these —
    it is the canonical, sole wire shape for that topic. trade-ledger is the
    sole BigQuery writer; its row builder (``writer.audit_log_to_row``) is
    schema-locked to these exact fields by
    ``tests/test_schema_parity.py::test_row_keys_match_schema_columns``.
    """

    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex}")
    source: str = Field(description="Emitting service, e.g. 'risk-engine' | 'ai-ops-agent'.")
    event_type: str = Field(description="e.g. 'limit_change' | 'kill_switch_reset' | 'proposal.risk_limit_change'.")
    actor: str | None = Field(default=None, description="'system' | 'claude@ai-ops' | 'admin@example.com'")
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    metadata: dict[str, Any] | None = None
    emitted_at: datetime
