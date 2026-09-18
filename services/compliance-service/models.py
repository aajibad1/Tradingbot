"""Wire schemas for KYC/KYB + sanctions/PEP screening (docs/09, contract §7.4).

Two distinct check types, each its own small state machine — deliberately NOT
reusing shared.http.Status (pending/processing/completed/failed): a KYC/KYB
review and a screening verdict are different vocabularies from a payment
lifecycle, and conflating them would blur what each status actually means.

SANDBOX ONLY. Every verdict here is a deterministic simulation, never a real
compliance determination — see main.py's module docstring and
docs/REGULATORY_BRIEF.md.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KycStatus:
    """A KYC/KYB check's lifecycle. PENDING -> IN_REVIEW -> APPROVED | REJECTED."""

    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"

    TERMINAL = frozenset({APPROVED, REJECTED})


class ScreeningVerdict:
    """A sanctions/PEP screening check's outcome. PENDING -> CLEAR | ESCALATED."""

    PENDING = "pending"
    CLEAR = "clear"
    ESCALATED = "escalated"

    TERMINAL = frozenset({CLEAR, ESCALATED})


class SubjectType:
    INDIVIDUAL = "individual"
    ORGANIZATION = "organization"

    ALL = frozenset({INDIVIDUAL, ORGANIZATION})


class KycCheckRequest(BaseModel):
    subject_id: str = Field(description="The beneficiary/organization id being checked")
    subject_type: str = Field(description="'individual' | 'organization'")
    tenant_id: str | None = None


class KycCheck(BaseModel):
    id: str
    subject_id: str
    subject_type: str
    status: str = Field(description="One of KycStatus")
    tenant_id: str | None = None
    correlation_id: str
    created_at: datetime
    updated_at: datetime

    def is_terminal(self) -> bool:
        return self.status in KycStatus.TERMINAL


class KycAdvanceRequest(BaseModel):
    outcome: str | None = Field(
        default=None,
        description="Injectable terminal outcome ('approved' | 'rejected') for "
                    "the IN_REVIEW -> terminal step. Ignored on the PENDING -> "
                    "IN_REVIEW step. Omitted = defaults to 'approved' (the "
                    "deterministic sandbox happy path).",
    )


class ScreeningCheckRequest(BaseModel):
    subject_id: str = Field(description="The beneficiary/organization id being screened")
    subject_type: str = Field(description="'individual' | 'organization'")
    tenant_id: str | None = None


class ScreeningCheck(BaseModel):
    id: str
    subject_id: str
    subject_type: str
    verdict: str = Field(description="One of ScreeningVerdict")
    tenant_id: str | None = None
    correlation_id: str
    created_at: datetime
    updated_at: datetime

    def is_terminal(self) -> bool:
        return self.verdict in ScreeningVerdict.TERMINAL

    def blocks_execution(self) -> bool:
        """True iff this check must block provider submission (contract's
        financial scenario matrix: "screening escalation -> manual review, no
        provider execution"). A non-terminal (still-pending) check ALSO blocks
        — the absence of a CLEAR verdict is never treated as clear."""
        return self.verdict != ScreeningVerdict.CLEAR


class ScreeningAdvanceRequest(BaseModel):
    outcome: str | None = Field(
        default=None,
        description="Injectable terminal verdict ('clear' | 'escalated'). "
                    "Omitted = defaults to 'clear' (the deterministic sandbox "
                    "happy path).",
    )
