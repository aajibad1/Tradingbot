"""Wire schemas for the payment-approval domain (issue #21, docs/adr/0009).

Distinct from services/approval-gate-service (docs/10 AI-agent-action
governance): that service classifies whether an *agent* may act. This one
governs whether a *payment* (an onramp/offramp order, or any other resource
type a caller names) may proceed — segregation-of-duties, not AI permission
tiers. Two different domains; docs/adr/0009 records why they aren't merged.

SANDBOX ONLY — no real payment execution is triggered by an approval here;
the caller (onramp/offramp-orchestrator) decides what "approved" unblocks.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ApprovalStatus:
    """An approval request's lifecycle."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"  # the attached quote expired before a decision landed

    TERMINAL = frozenset({APPROVED, REJECTED, EXPIRED})


class Decision:
    APPROVE = "approve"
    REJECT = "reject"

    ALL = frozenset({APPROVE, REJECT})


def _reject_empty(v: str | None, field_name: str) -> str | None:
    # An empty string is a realistic client-input shape (some frameworks
    # default an unset optional field to "" rather than omitting it or
    # sending null) and must never be silently treated the same as the
    # field being genuinely absent — see docs/adr/0004's "Consequences" and
    # the review history on PR #23/#25 for why this class of bug matters
    # here specifically: an accidentally-empty requested_by/tenant_id could
    # otherwise corrupt the self-approval / tenant-isolation checks below.
    if v is not None and v == "":
        raise ValueError(f"{field_name} must not be empty — omit the field entirely if not applicable")
    return v


class ApprovalRequestCreate(BaseModel):
    resource_type: str = Field(description="e.g. 'onramp_order' | 'offramp_order'")
    resource_id: str = Field(description="The id of the thing being approved")
    requested_by: str = Field(description="User id of the requester — never a valid approver for this request")
    tenant_id: str | None = None
    required_approvals: int = Field(default=1, ge=1, description="Segregation-of-duties: minimum DISTINCT approvers")
    authorized_approvers: list[str] = Field(
        default_factory=list,
        description="User ids permitted to decide. Empty = any user except requested_by.",
    )
    quote_expires_at: datetime | None = Field(
        default=None,
        description="If set, a decision recorded at/after this time is refused and the "
                    "request moves to 'expired' instead — 'quote expires before approval' "
                    "in the contract's financial scenario matrix (§11.2).",
    )
    summary: str = Field(
        default="", description="Route/cost/timing explanation the approver sees (contract §7.1)"
    )

    @field_validator("resource_id", "requested_by", "tenant_id")
    @classmethod
    def _no_empty_ids(cls, v: str | None) -> str | None:
        return _reject_empty(v, "id field")

    @field_validator("authorized_approvers")
    @classmethod
    def _no_empty_approver_ids(cls, v: list[str]) -> list[str]:
        for a in v:
            _reject_empty(a, "authorized_approvers entry")
        return v


class ApprovalDecision(BaseModel):
    decided_by: str = Field(description="User id making the decision")
    decision: str = Field(description="'approve' | 'reject'")
    reason: str = Field(default="")

    @field_validator("decided_by")
    @classmethod
    def _no_empty_decided_by(cls, v: str) -> str:
        result = _reject_empty(v, "decided_by")
        assert result is not None  # decided_by is required, not Optional
        return result


class ApprovalVote(BaseModel):
    decided_by: str
    decision: str
    reason: str = ""
    decided_at: datetime


class ApprovalRequestRecord(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    requested_by: str
    tenant_id: str | None = None
    required_approvals: int
    authorized_approvers: list[str] = Field(default_factory=list)
    quote_expires_at: datetime | None = None
    summary: str = ""
    status: str = Field(description="One of ApprovalStatus")
    votes: list[ApprovalVote] = Field(default_factory=list)
    correlation_id: str
    created_at: datetime
    updated_at: datetime

    def is_terminal(self) -> bool:
        return self.status in ApprovalStatus.TERMINAL

    def is_approved(self) -> bool:
        return self.status == ApprovalStatus.APPROVED
