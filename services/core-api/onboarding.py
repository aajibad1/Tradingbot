"""Onboarding state machine + regional policy hook.

Two concerns, kept separate:
  * the STATE MACHINE — which transitions are legal (enforced, not assumed).
  * the POLICY ENGINE — given (market, country, kyc), is the tenant eligible to
    trade, does it need review, or is it restricted. This is a deliberate stub:
    the real engine grows country rules, sanctions/AML screening, and venue
    eligibility (the Africa/Global "policy packs"). It is isolated here so those
    can be filled in without touching the API surface.

Nothing here promotes a tenant to LIVE — a TRADING_READY tenant trades PAPER by
default; live is gated separately per TRADING_ASSUMPTIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from db import KycStatus, Market, OnboardingStatus as S

# Legal transitions. A state's value is the set of states reachable from it.
# Billing/funding rungs exist in the enum but aren't wired until Stripe + the
# custody decision land, so they're intentionally absent from the live graph.
_TRANSITIONS: dict[S, set[S]] = {
    S.ACCOUNT_CREATED: {S.IDENTITY_PENDING, S.RESTRICTED},
    S.IDENTITY_PENDING: {S.IDENTITY_VERIFIED, S.REVIEW_PENDING, S.RESTRICTED},
    S.IDENTITY_VERIFIED: {S.TRADING_READY, S.REVIEW_PENDING, S.RESTRICTED},
    S.REVIEW_PENDING: {S.TRADING_READY, S.RESTRICTED},
    S.TRADING_READY: {S.RESTRICTED},          # can always be restricted (compliance)
    S.RESTRICTED: {S.REVIEW_PENDING},         # re-review after a restriction
}


class TransitionError(ValueError):
    pass


def assert_transition(frm: S, to: S) -> None:
    if to not in _TRANSITIONS.get(frm, set()):
        raise TransitionError(f"illegal onboarding transition {frm.value} -> {to.value}")


# --- Regional policy (stub) ----------------------------------------------------

# Launch countries per the brief; others fall to manual review rather than auto-pass.
_AFRICA_LAUNCH = {"NG", "ZA", "KE", "GH"}      # Nigeria, South Africa, Kenya, Ghana
# Hard blocks (sanctions/embargo placeholder — the real list comes from screening).
_BLOCKED = {"KP", "IR", "SY", "CU"}


@dataclass
class PolicyDecision:
    final_status: S
    required_controls: list[str] = field(default_factory=list)
    reason: str = ""


def evaluate(market: Market | None, country: str | None, kyc: KycStatus) -> PolicyDecision:
    """Decide the post-submission onboarding status. Conservative by default:
    anything unrecognized goes to review, never silently to trading_ready."""
    cc = (country or "").upper()
    if cc in _BLOCKED:
        return PolicyDecision(S.RESTRICTED, reason=f"jurisdiction {cc} is blocked")
    if kyc is not KycStatus.VERIFIED:
        return PolicyDecision(S.REVIEW_PENDING, ["kyc"], reason="KYC not verified")
    if market is None or not cc:
        return PolicyDecision(S.REVIEW_PENDING, reason="market/country not set")
    if market is Market.AFRICA and cc not in _AFRICA_LAUNCH:
        return PolicyDecision(
            S.REVIEW_PENDING, ["corridor_review"],
            reason=f"{cc} not in the Africa launch set — manual corridor review",
        )
    return PolicyDecision(S.TRADING_READY, reason="cleared regional policy (paper)")
