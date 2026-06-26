"""approval-gate-service — governance gate for AI/agent actions (docs/10).

Every proposed agent action passes through here BEFORE it can act. The gate
enforces the AI permission model (policy.py): read-only → auto-approved,
sensitive → held for a human, withdrawals/leverage → hard-blocked. Each proposal
records the model_version, prompt_version, and evidence behind it (docs/10
traceability), and every decision is auditable.

This is a safety control, not an executor: it never performs the action. It
returns a verdict that an executor must honor — approved proposals are a signal,
not an action. Blocked proposals can never be approved here.

Endpoints:
  GET  /healthz
  POST /v1/proposals                  submit an action for governance
  GET  /v1/proposals/{id}
  GET  /v1/proposals?status=&agent=
  POST /v1/proposals/{id}/decide      {decision: approve|reject, decided_by, reason}
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

import policy
from shared.a2a import (
    A2AError,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Message,
    base_url,
    data_part,
    install_a2a,
    text_part,
)
from shared.a2a import errors as a2a_errors
from shared.http import APIError, install_contract
from shared.pubsub.publisher import Topic, get_publisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("approval-gate-service")

PRODUCER = "approval-gate-service"

# Proposal status values.
S_AUTO = policy.AUTO            # auto_approved
S_PENDING = policy.PROPOSE      # pending_approval
S_BLOCKED = policy.BLOCKED      # blocked
S_APPROVED = "approved"
S_REJECTED = "rejected"

_proposals: dict[str, dict] = {}

app = FastAPI(title="approval-gate-service", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event_type: str, proposal: dict) -> None:
    try:
        get_publisher().publish_event(
            Topic.AUDIT_LOG, event_type, proposal,
            producer=PRODUCER, tenant_id=proposal.get("tenant_id"),
        )
    except Exception:  # noqa: BLE001 — audit is best-effort, never blocks the gate
        logger.warning("governance audit emit failed for %s (non-fatal)", proposal.get("id"))


class ProposalCreate(BaseModel):
    agent: str = Field(description="Proposing agent, e.g. 'opportunity-ranker'")
    action_type: str = Field(description="e.g. read | risk_limit_change | withdrawal")
    summary: str = Field(description="Human-readable description of the proposed action")
    payload: dict[str, Any] = Field(default_factory=dict)
    model_version: str | None = None
    prompt_version: str | None = None
    evidence: list[str] = Field(default_factory=list, description="Citations/links backing the proposal")
    tenant_id: str | None = None


class Decision(BaseModel):
    decision: str = Field(description="approve | reject")
    decided_by: str
    reason: str = ""


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/proposals")
def submit(req: ProposalCreate) -> dict[str, Any]:
    classification = policy.classify(req.action_type)
    now = _now()
    proposal = {
        "id": f"prop_{uuid.uuid4().hex[:16]}",
        "agent": req.agent,
        "action_type": req.action_type,
        "summary": req.summary,
        "payload": req.payload,
        "model_version": req.model_version,
        "prompt_version": req.prompt_version,
        "evidence": req.evidence,
        "tenant_id": req.tenant_id,
        "classification": classification,
        "status": classification,  # auto_approved / pending_approval / blocked
        "decided_by": "policy" if classification in (S_AUTO, S_BLOCKED) else None,
        "decided_at": now if classification in (S_AUTO, S_BLOCKED) else None,
        "reason": ("read-only — autonomous" if classification == S_AUTO else
                   "hardcoded denial (withdrawals/leverage never AI-executable)"
                   if classification == S_BLOCKED else None),
        "created_at": now,
    }
    _proposals[proposal["id"]] = proposal
    _emit("agent.proposal_created", proposal)
    if classification == S_BLOCKED:
        logger.warning("BLOCKED proposal agent=%s action=%s", req.agent, req.action_type)
    return proposal


def _get(proposal_id: str) -> dict[str, Any]:
    p = _proposals.get(proposal_id)
    if p is None:
        raise APIError("proposal_not_found", f"proposal {proposal_id} not found", http_status=404)
    return p


@app.get("/v1/proposals/{proposal_id}")
def get_proposal(proposal_id: str) -> dict[str, Any]:
    return _get(proposal_id)


@app.get("/v1/proposals")
def list_proposals(status: str | None = None, agent: str | None = None) -> dict[str, Any]:
    out = [p for p in _proposals.values()
           if (status is None or p["status"] == status) and (agent is None or p["agent"] == agent)]
    return {"proposals": out, "count": len(out)}


@app.post("/v1/proposals/{proposal_id}/decide")
def decide(proposal_id: str, req: Decision) -> dict[str, Any]:
    p = _get(proposal_id)
    if p["status"] == S_BLOCKED:
        raise APIError("action_blocked",
                       "this action is hardcoded-blocked and cannot be approved", http_status=409)
    if p["status"] != S_PENDING:
        raise APIError("not_pending",
                       f"proposal is {p['status']!r}; only pending_approval can be decided", http_status=409)
    if req.decision not in ("approve", "reject"):
        raise APIError("invalid_decision", "decision must be approve|reject", http_status=422)
    p["status"] = S_APPROVED if req.decision == "approve" else S_REJECTED
    p["decided_by"] = req.decided_by
    p["decided_at"] = _now()
    p["reason"] = req.reason
    _proposals[proposal_id] = p
    _emit("agent.proposal_decided", p)
    return p


# --- A2A (Agent2Agent) surface ------------------------------------------------
# The execution-guard / permission-model gate (docs/10) exposed over A2A: a peer
# agent proposes an action (data part with ProposalCreate fields); we classify it
# under the permission model and return the decision — auto_approved (read),
# pending_approval (sensitive → human), or blocked (withdrawals/leverage). The
# hardcoded-block invariant holds regardless of transport.
_A2A_CARD = AgentCard(
    name="approval-gate-service",
    description=("AI permission-model gate: classify a proposed agent action as "
                 "auto-approved (read-only), pending human approval (sensitive), or "
                 "hardcoded-blocked (withdrawals/leverage). Returns the proposal record."),
    url=f"{base_url('approval-gate-service')}/a2a",
    version=app.version,
    provider=AgentProvider(organization="traditbot"),
    skills=[AgentSkill(
        id="evaluate-action",
        name="Evaluate a proposed action",
        description=("Submit a proposed action under the permission model and receive "
                     "its classification + decision. Withdrawals/leverage are never "
                     "AI-executable."),
        tags=["governance", "permissions", "approval", "safety"],
        examples=['{"agent":"opportunity-ranker","action_type":"read","summary":"read funding rates"}'],
    )],
)


def _a2a_handle(message: Message):
    data = message.data()
    if data is None:
        raise A2AError(a2a_errors.INVALID_PARAMS,
                       "data part with proposal fields (agent, action_type, summary) is required")
    try:
        req = ProposalCreate(**data)
    except Exception as exc:  # noqa: BLE001 — bad proposal shape is invalid params
        raise A2AError(a2a_errors.INVALID_PARAMS, f"invalid proposal: {exc}") from exc
    proposal = submit(req)
    summary = f"{proposal['classification']}: {req.action_type} proposed by {req.agent}"
    return [text_part(summary), data_part(proposal)]


install_a2a(app, card=_A2A_CARD, handler=_a2a_handle)
