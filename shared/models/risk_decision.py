"""RiskDecision — the audit + ML fact for one risk-engine evaluation.

AI Phase A is *instrument first*: capture every deterministic decision (approve
AND reject) with its decision-time feature snapshot, so the gate's behavior
becomes labeled training data from day one — long before any model is trained.

Why a dedicated fact (vs. reusing Opportunity / risk alerts):
  - Opportunities are *detections*; this is the *decision* over one — including
    the rejects, which are exactly the negatives a learned ranker needs.
  - risk alerts (arb-risk-alerts) fire only on breaches; they are not a complete
    decision stream and carry no feature snapshot.

The realized **label** (net PnL, did-it-fill, slippage) is not stored here — it
joins back later via ``opportunity_id`` against ``arb_trading.trades``. Keeping
features and outcome in separate tables (joined at train time) is deliberate:
the decision is immutable at emit time; the outcome accrues over the trade's life.

Advisory fields (action/probability/model_version) come from the opportunity-ranker
and are FAIL-OPEN — absent when the ranker is unavailable. They never gate; they
are recorded so we can later measure the advisory model against realized outcomes.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from shared.models.opportunity import StrategyType
from shared.tenant import DEFAULT_TENANT


class RiskDecision(BaseModel):
    """One risk-engine decision over one opportunity (approve or reject)."""

    decision_id: str = Field(description="Stable id for this decision (derived from opportunity_id)")
    opportunity_id: str = Field(description="Join key to the detection and, later, the realized trade")
    tenant_id: str = Field(default=DEFAULT_TENANT)

    strategy: StrategyType
    asset: str
    long_exchange: str
    short_exchange: str
    directional: bool = Field(default=False, description="True for the directional sleeve (not market-neutral)")

    # --- decision-time feature snapshot (the model inputs) ---
    net_edge_bps: float
    gross_spread_bps: float
    trading_fees_bps: float
    slippage_estimate_bps: float
    funding_rate_annualized_pct: float = 0.0
    confidence_score: float = Field(ge=0.0, le=1.0)
    recommended_size_usd: float = Field(gt=0.0)

    # --- the decision (the eventual classification target's deterministic prior) ---
    approved: bool
    kill_switch_active: bool = False
    violation_rules: list[str] = Field(
        default_factory=list,
        description="RiskRule values that fired; empty when approved",
    )

    # --- advisory annotation (fail-open; may be absent) ---
    advisory_action: str | None = None
    advisory_probability: float | None = None
    model_version: str | None = None

    decided_at: datetime
