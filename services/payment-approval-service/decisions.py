"""Payment-approval state machine — segregation-of-duties, pure logic.

No I/O, no audit calls here (main.py owns instrumentation — every denial path
below is a real security-relevant event and main.py audits it, success or
not; this module's only job is getting the state machine right).
"""

from __future__ import annotations

from datetime import datetime

from models import ApprovalRequestRecord, ApprovalStatus, ApprovalVote, Decision


class DecisionError(Exception):
    """A decision attempt that must not change who has and hasn't approved —
    self-approval, an unauthorized approver, an already-terminal request, an
    expired quote, or a malformed decision value. Every one of these is
    audited by the caller regardless of being "just" an error."""

    def __init__(self, code: str, message: str, *, http_status: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def apply_decision(record: ApprovalRequestRecord, *, decided_by: str, decision: str,
                    reason: str, now: datetime) -> ApprovalRequestRecord:
    """Validate and apply one decision to `record` in place, returning it.

    Order of checks matters: terminal / expiry are checked before who's
    deciding, since "this request can no longer be decided on at all" is a
    different failure from "you specifically can't decide on it."
    """
    if record.is_terminal():
        raise DecisionError(
            "not_pending",
            f"approval request {record.id} is already {record.status!r}",
            http_status=409,
        )
    if record.quote_expires_at is not None and now >= record.quote_expires_at:
        # The quote this approval was scoped to no longer applies — expire
        # the request itself (terminal) rather than let a stale decision
        # land. Contract §11.2: "Quote expires before approval -> Payment
        # cannot proceed without a new quote."
        record.status = ApprovalStatus.EXPIRED
        record.updated_at = now
        raise DecisionError(
            "quote_expired",
            "the quote attached to this approval request has expired — "
            "a new approval request (against a fresh quote) is required",
            http_status=409,
        )
    if decision not in Decision.ALL:
        raise DecisionError(
            "invalid_decision",
            f"decision must be one of {sorted(Decision.ALL)}, got {decision!r}",
            http_status=422,
        )
    if decided_by == record.requested_by:
        # Segregation-of-duties, unconditional — no authorized_approvers
        # list can override this; the requester is never a valid approver
        # for their own request.
        raise DecisionError(
            "self_approval_denied",
            "the requester cannot approve or reject their own request",
            http_status=403,
        )
    if record.authorized_approvers and decided_by not in record.authorized_approvers:
        raise DecisionError(
            "unauthorized_approver",
            f"{decided_by!r} is not an authorized approver for this request",
            http_status=403,
        )

    record.votes.append(ApprovalVote(decided_by=decided_by, decision=decision,
                                      reason=reason, decided_at=now))
    record.updated_at = now
    if decision == Decision.REJECT:
        # A single rejection is terminal — dual control means N approvals
        # are required to proceed, not N non-rejections; one objection stops it.
        record.status = ApprovalStatus.REJECTED
        return record

    distinct_approvers = {v.decided_by for v in record.votes if v.decision == Decision.APPROVE}
    if len(distinct_approvers) >= record.required_approvals:
        record.status = ApprovalStatus.APPROVED
    return record
