"""payment-approval-service — segregation-of-duties approval workflow for
payments (issue #21, docs/adr/0009).

Distinct from services/approval-gate-service (docs/10): that service
classifies whether an AI AGENT may act (permission tiers: auto/propose/
blocked). This one governs whether a PAYMENT (an onramp/offramp order, or
any resource type a caller names) may proceed — human segregation-of-duties,
not AI permission. docs/adr/0009 records why these are two domain models,
not one.

Core guarantee: the requester can never be a valid approver for their own
request (self-approval is unconditionally denied), and every decision —
approved, rejected, or denied — publishes a shared.models.AuditLogEntry.

Endpoints:
  GET  /healthz
  POST /v1/approvals                    create a request (requested_by, resource, ...)
  GET  /v1/approvals/{id}
  POST /v1/approvals/{id}/decide        {decided_by, decision, reason?}

SANDBOX-scoped state (in-memory, single-instance) — matches every other
sandbox service in this repo (onramp/offramp-orchestrator, compliance-service).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request

from decisions import DecisionError, apply_decision
from models import ApprovalDecision, ApprovalRequestCreate, ApprovalRequestRecord
from shared.http import APIError, get_correlation_id, install_contract
from shared.models.audit_log_entry import AuditLogEntry
from shared.pubsub.publisher import Topic, get_publisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("payment-approval-service")

PRODUCER = "payment-approval-service"

app = FastAPI(title="payment-approval-service", version="0.1.0")
install_contract(app, service_name=PRODUCER)

# In-memory state (single-instance sandbox) — matches onramp/offramp/compliance.
_requests: dict[str, ApprovalRequestRecord] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _audit(*, event_type: str, action: str, record: ApprovalRequestRecord,
           actor: str | None, outcome_code: str | None) -> None:
    """Every decision attempt is audited — approved, rejected, AND denied
    (self-approval, unauthorized, expired, already-terminal). Fail-open:
    an audit-publish hiccup must never block the (already-applied or
    already-refused) decision, matching every other audit call site in
    this repo (compliance-service, kill_switch.py, ai-ops-agent)."""
    try:
        get_publisher().publish(
            Topic.AUDIT_LOG,
            AuditLogEntry(
                source=PRODUCER,
                event_type=event_type,
                actor=actor,
                action=action,
                resource_type="payment_approval",
                resource_id=record.id,
                metadata={
                    "status": record.status,
                    "target_resource_type": record.resource_type,
                    "target_resource_id": record.resource_id,
                    "requested_by": record.requested_by,
                    "tenant_id": record.tenant_id,
                    "outcome_code": outcome_code,
                },
                emitted_at=_now(),
            ),
            attributes={"source": PRODUCER, "event": event_type},
        )
    except Exception:  # noqa: BLE001 — audit is best-effort, never blocks the decision
        logger.warning("audit emit failed for approval %s (non-fatal)", record.id)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/approvals", response_model=ApprovalRequestRecord)
def create_approval(req: ApprovalRequestCreate, request: Request) -> ApprovalRequestRecord:
    now = _now()
    record = ApprovalRequestRecord(
        id=_new_id("apr"),
        resource_type=req.resource_type,
        resource_id=req.resource_id,
        requested_by=req.requested_by,
        tenant_id=req.tenant_id,
        required_approvals=req.required_approvals,
        authorized_approvers=req.authorized_approvers,
        quote_expires_at=req.quote_expires_at,
        summary=req.summary,
        status="pending",
        votes=[],
        correlation_id=get_correlation_id(request) or _new_id("corr"),
        created_at=now,
        updated_at=now,
    )
    _requests[record.id] = record
    _audit(event_type="approval.requested", action="create", record=record,
           actor=req.requested_by, outcome_code=None)
    return record


def _get_request(request_id: str) -> ApprovalRequestRecord:
    record = _requests.get(request_id)
    if record is None:
        raise APIError("approval_request_not_found", f"approval request {request_id} not found", http_status=404)
    return record


@app.get("/v1/approvals/{request_id}", response_model=ApprovalRequestRecord)
def get_approval(request_id: str) -> ApprovalRequestRecord:
    return _get_request(request_id)


@app.post("/v1/approvals/{request_id}/decide", response_model=ApprovalRequestRecord)
def decide_approval(request_id: str, req: ApprovalDecision) -> ApprovalRequestRecord:
    record = _get_request(request_id)
    try:
        apply_decision(record, decided_by=req.decided_by, decision=req.decision,
                        reason=req.reason, now=_now())
    except DecisionError as exc:
        _requests[request_id] = record  # persist any mutation the failed attempt made (e.g. expiry)
        _audit(event_type=f"approval.decision_denied.{exc.code}", action="decide",
               record=record, actor=req.decided_by, outcome_code=exc.code)
        raise APIError(exc.code, exc.message, http_status=exc.http_status) from exc
    _requests[request_id] = record
    _audit(event_type=f"approval.{record.status}", action="decide", record=record,
           actor=req.decided_by, outcome_code=record.status)
    return record
