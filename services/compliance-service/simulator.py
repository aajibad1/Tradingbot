"""Deterministic KYC/KYB + screening simulators. NO real compliance decision.

Mirrors services/onramp-orchestrator/sandbox_provider.py's style: no wall-clock
timers, no external calls — the state advances only on an explicit ``advance``
call, with an injectable outcome, so tests and demos are fully reproducible.

KYC/KYB: PENDING -> IN_REVIEW -> APPROVED | REJECTED (two advance calls — the
acceptance criteria's literal state machine: the first call always moves to
IN_REVIEW regardless of outcome; the second consumes ``outcome`` to reach a
terminal state).

Screening: PENDING -> CLEAR | ESCALATED (one advance call — this repo's other
sandbox simulators use exactly the number of steps their real-world analog
needs, and a screening check doesn't have a meaningful separate "in review"
state a caller can observe/act on differently).
"""

from __future__ import annotations

from shared.http import APIError

from models import KycStatus, ScreeningVerdict

_KYC_TERMINAL_OUTCOMES = frozenset({KycStatus.APPROVED, KycStatus.REJECTED})
_SCREENING_TERMINAL_OUTCOMES = frozenset({ScreeningVerdict.CLEAR, ScreeningVerdict.ESCALATED})


def next_kyc_status(current: str, outcome: str | None) -> str:
    """The next KYC/KYB status, or the same status if already terminal.

    outcome is only consulted on the IN_REVIEW -> terminal step; it's ignored
    (not merely unused) on PENDING -> IN_REVIEW, matching the acceptance
    criteria's two-step shape — a caller can't skip the review step by
    supplying an outcome on the first call."""
    if current in KycStatus.TERMINAL:
        return current
    if current == KycStatus.PENDING:
        return KycStatus.IN_REVIEW
    if current == KycStatus.IN_REVIEW:
        resolved = outcome or KycStatus.APPROVED
        if resolved not in _KYC_TERMINAL_OUTCOMES:
            raise APIError(
                "invalid_outcome",
                f"outcome must be one of {sorted(_KYC_TERMINAL_OUTCOMES)}, got {outcome!r}",
                http_status=422,
            )
        return resolved
    # Unreachable with the statuses this module defines — fail loud rather
    # than silently returning an unrecognized status.
    raise APIError("invalid_status", f"unrecognized KYC status {current!r}", http_status=500)


def next_screening_verdict(current: str, outcome: str | None) -> str:
    """The next screening verdict, or the same verdict if already terminal."""
    if current in ScreeningVerdict.TERMINAL:
        return current
    resolved = outcome or ScreeningVerdict.CLEAR
    if resolved not in _SCREENING_TERMINAL_OUTCOMES:
        raise APIError(
            "invalid_outcome",
            f"outcome must be one of {sorted(_SCREENING_TERMINAL_OUTCOMES)}, got {outcome!r}",
            http_status=422,
        )
    return resolved
