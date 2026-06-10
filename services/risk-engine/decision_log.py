"""Build the RiskDecision fact from an evaluation result (AI Phase A).

Kept as a pure function (no Pub/Sub, no clock dependency unless ``now`` is
omitted) so the feature-snapshot mapping is unit-tested directly. ``main._on_opportunity``
calls this once per evaluation and publishes the result FAIL-OPEN — instrumentation
must never break the choke point.
"""

from __future__ import annotations

from datetime import UTC, datetime

from models import EvaluateResponse

from shared.models.opportunity import Opportunity, StrategyType
from shared.models.risk_decision import RiskDecision


def build_risk_decision(
    opp: Opportunity,
    result: EvaluateResponse,
    ranking: dict | None = None,
    now: datetime | None = None,
) -> RiskDecision:
    """Snapshot the decision-time features + the deterministic verdict.

    ``ranking`` is the advisory opportunity-ranker payload (fail-open; may be
    None or partial). ``now`` is injectable for deterministic tests.
    """
    rank = ranking or {}
    prob = rank.get("profitability_probability")
    return RiskDecision(
        decision_id=f"{opp.id}:rd",
        opportunity_id=opp.id,
        tenant_id=opp.user_id,
        strategy=opp.strategy,
        asset=opp.asset,
        long_exchange=opp.long_exchange,
        short_exchange=opp.short_exchange,
        directional=opp.strategy is StrategyType.DIRECTIONAL,
        net_edge_bps=opp.net_edge_bps,
        gross_spread_bps=opp.gross_spread_bps,
        trading_fees_bps=opp.trading_fees_bps,
        slippage_estimate_bps=opp.slippage_estimate_bps,
        funding_rate_annualized_pct=opp.funding_rate_annualized_pct,
        confidence_score=opp.confidence_score,
        recommended_size_usd=opp.recommended_size_usd,
        approved=result.approved,
        kill_switch_active=result.kill_switch_active,
        violation_rules=[v.rule.value for v in result.violations],
        advisory_action=(str(rank["recommended_action"]) if rank.get("recommended_action") else None),
        advisory_probability=(float(prob) if prob is not None else None),
        model_version=(str(rank["model_version"]) if rank.get("model_version") else None),
        decided_at=now or datetime.now(UTC),
    )
