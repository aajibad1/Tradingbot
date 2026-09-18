"""compliance-service — KYC/KYB + sanctions/PEP screening (docs/09, contract §7.4).

SANDBOX ONLY. Every check here is a deterministic simulation — this service
NEVER makes or implies a real compliance/legal determination, and nothing in
this file should be read as such. Real vendor integration requires a legal and
partner decision not yet made (docs/REGULATORY_BRIEF.md); until then this is
the sandbox adapter the contract's §7.4 MVP scope requires, modeled on
services/onramp-orchestrator/sandbox_provider.py's deterministic style
(docs/adr/0007's adapter philosophy — swappable for a real vendor later
without touching callers).

Two independent check types:
  - KYC/KYB status: PENDING -> IN_REVIEW -> APPROVED | REJECTED
  - Screening (sanctions/PEP): PENDING -> CLEAR | ESCALATED

Every status/verdict transition publishes a shared.models.AuditLogEntry to
Topic.AUDIT_LOG — trade-ledger is the sole BigQuery writer for that stream
(docs/adr/0008). This is what makes "every status change is audited"
(contract acceptance criteria) a real, verifiable guarantee, not a claim.

Endpoints:
  GET  /healthz
  POST /v1/kyc/checks                        {subject_id, subject_type, tenant_id?}
  GET  /v1/kyc/checks/{id}
  POST /v1/kyc/checks/{id}/advance           {outcome?}  ('approved'|'rejected', only consulted at IN_REVIEW)
  POST /v1/screening/checks                  {subject_id, subject_type, tenant_id?}
  GET  /v1/screening/checks/{id}
  POST /v1/screening/checks/{id}/advance     {outcome?}  ('clear'|'escalated')

State is in-memory (single-instance sandbox), matching onramp/offramp-orchestrator.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request

import simulator
from models import (
    KycAdvanceRequest,
    KycCheck,
    KycCheckRequest,
    KycStatus,
    ScreeningAdvanceRequest,
    ScreeningCheck,
    ScreeningCheckRequest,
    ScreeningVerdict,
    SubjectType,
)
from shared.http import APIError, get_correlation_id, install_contract
from shared.models.audit_log_entry import AuditLogEntry
from shared.pubsub.publisher import Topic, get_publisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("compliance-service")

PRODUCER = "compliance-service"

app = FastAPI(title="compliance-service", version="0.1.0")
install_contract(app, service_name=PRODUCER)

# In-memory state (single-instance sandbox) — matches onramp/offramp-orchestrator.
_kyc_checks: dict[str, KycCheck] = {}
_screening_checks: dict[str, ScreeningCheck] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _validate_subject_type(subject_type: str) -> None:
    if subject_type not in SubjectType.ALL:
        raise APIError(
            "invalid_subject_type",
            f"subject_type must be one of {sorted(SubjectType.ALL)}, got {subject_type!r}",
            http_status=422,
        )


def _audit(*, event_type: str, action: str, resource_type: str, resource_id: str,
           status_field: str, status_value: str, subject_id: str, subject_type: str,
           tenant_id: str | None) -> None:
    """Every KYC/screening transition is audited — fail-open: an audit-publish
    hiccup must never block the (already-applied) status change, matching this
    repo's established convention for instrumentation vs. the state change it
    describes (see e.g. trade-ledger's RiskDecision fact publishing)."""
    try:
        get_publisher().publish(
            Topic.AUDIT_LOG,
            AuditLogEntry(
                source=PRODUCER,
                event_type=event_type,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata={status_field: status_value, "subject_id": subject_id,
                          "subject_type": subject_type, "tenant_id": tenant_id},
                emitted_at=_now(),
            ),
            attributes={"source": PRODUCER, "event": event_type},
        )
    except Exception:  # noqa: BLE001 — audit is best-effort, never blocks the transition
        logger.warning("audit emit failed for %s %s (non-fatal)", resource_type, resource_id)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# --- KYC/KYB ----------------------------------------------------------------- #


@app.post("/v1/kyc/checks", response_model=KycCheck)
def create_kyc_check(req: KycCheckRequest, request: Request) -> KycCheck:
    _validate_subject_type(req.subject_type)
    now = _now()
    check = KycCheck(
        id=_new_id("kyc"),
        subject_id=req.subject_id,
        subject_type=req.subject_type,
        status=KycStatus.PENDING,
        tenant_id=req.tenant_id,
        correlation_id=get_correlation_id(request) or _new_id("corr"),
        created_at=now,
        updated_at=now,
    )
    _kyc_checks[check.id] = check
    _audit(event_type="kyc.check_created", action="create", resource_type="kyc_check",
           resource_id=check.id, status_field="status", status_value=check.status,
           subject_id=check.subject_id, subject_type=check.subject_type, tenant_id=check.tenant_id)
    return check


def _get_kyc_check(check_id: str) -> KycCheck:
    check = _kyc_checks.get(check_id)
    if check is None:
        raise APIError("kyc_check_not_found", f"KYC check {check_id} not found", http_status=404)
    return check


@app.get("/v1/kyc/checks/{check_id}", response_model=KycCheck)
def get_kyc_check(check_id: str) -> KycCheck:
    return _get_kyc_check(check_id)


@app.post("/v1/kyc/checks/{check_id}/advance", response_model=KycCheck)
def advance_kyc_check(check_id: str, req: KycAdvanceRequest) -> KycCheck:
    """Advance the sandbox KYC/KYB check one step. A terminal check is
    returned unchanged (idempotent, matching onramp/offramp's advance())."""
    check = _get_kyc_check(check_id)
    if check.is_terminal():
        return check
    new_status = simulator.next_kyc_status(check.status, req.outcome)
    check.status = new_status
    check.updated_at = _now()
    _kyc_checks[check_id] = check
    _audit(event_type=f"kyc.{new_status}", action="advance", resource_type="kyc_check",
           resource_id=check.id, status_field="status", status_value=new_status,
           subject_id=check.subject_id, subject_type=check.subject_type, tenant_id=check.tenant_id)
    return check


# --- Screening (sanctions/PEP) ------------------------------------------------ #


@app.post("/v1/screening/checks", response_model=ScreeningCheck)
def create_screening_check(req: ScreeningCheckRequest, request: Request) -> ScreeningCheck:
    _validate_subject_type(req.subject_type)
    now = _now()
    check = ScreeningCheck(
        id=_new_id("scr"),
        subject_id=req.subject_id,
        subject_type=req.subject_type,
        verdict=ScreeningVerdict.PENDING,
        tenant_id=req.tenant_id,
        correlation_id=get_correlation_id(request) or _new_id("corr"),
        created_at=now,
        updated_at=now,
    )
    _screening_checks[check.id] = check
    _audit(event_type="screening.check_created", action="create", resource_type="screening_check",
           resource_id=check.id, status_field="verdict", status_value=check.verdict,
           subject_id=check.subject_id, subject_type=check.subject_type, tenant_id=check.tenant_id)
    return check


def _get_screening_check(check_id: str) -> ScreeningCheck:
    check = _screening_checks.get(check_id)
    if check is None:
        raise APIError("screening_check_not_found", f"screening check {check_id} not found", http_status=404)
    return check


@app.get("/v1/screening/checks/{check_id}", response_model=ScreeningCheck)
def get_screening_check(check_id: str) -> ScreeningCheck:
    return _get_screening_check(check_id)


@app.post("/v1/screening/checks/{check_id}/advance", response_model=ScreeningCheck)
def advance_screening_check(check_id: str, req: ScreeningAdvanceRequest) -> ScreeningCheck:
    check = _get_screening_check(check_id)
    if check.is_terminal():
        return check
    new_verdict = simulator.next_screening_verdict(check.verdict, req.outcome)
    check.verdict = new_verdict
    check.updated_at = _now()
    _screening_checks[check_id] = check
    event_type = "screening.escalated" if new_verdict == ScreeningVerdict.ESCALATED else "screening.clear"
    _audit(event_type=event_type, action="advance", resource_type="screening_check",
           resource_id=check.id, status_field="verdict", status_value=new_verdict,
           subject_id=check.subject_id, subject_type=check.subject_type, tenant_id=check.tenant_id)
    return check
