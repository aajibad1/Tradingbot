"""Unit tests for the RiskDecision fact builder (AI Phase A, pure function)."""

from __future__ import annotations

from datetime import datetime

from decision_log import build_risk_decision
from models import EvaluateResponse

from shared.models.opportunity import Opportunity, StrategyType
from shared.models.risk_state import RiskRule, RiskViolation


def _opp(strategy: StrategyType = StrategyType.FUNDING_RATE_ARB) -> Opportunity:
    return Opportunity(
        id="o9", user_id="tenant-x", strategy=strategy, asset="ETH",
        long_exchange="kraken", short_exchange="hyperliquid",
        gross_spread_bps=80.0, trading_fees_bps=31.0, slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=18.0, net_edge_bps=55.0, confidence_score=0.6,
        recommended_size_usd=2_000.0, min_hold_hours=6.0, detected_at=datetime(2026, 1, 1),
    )


_NOW = datetime(2026, 1, 2, 3, 4, 5)


def test_approved_decision_snapshots_features() -> None:
    d = build_risk_decision(_opp(), EvaluateResponse(approved=True), now=_NOW)
    assert d.decision_id == "o9:rd"
    assert d.opportunity_id == "o9"
    assert d.tenant_id == "tenant-x"
    assert d.approved is True
    assert d.violation_rules == []
    assert d.net_edge_bps == 55.0
    assert d.confidence_score == 0.6
    assert d.directional is False
    assert d.decided_at == _NOW


def test_rejected_decision_records_violation_rules() -> None:
    result = EvaluateResponse(
        approved=False,
        violations=[RiskViolation(rule=RiskRule.PER_TRADE_SIZE_LIMIT,
                                  observed=10_000.0, limit=5_000.0, message="too big")],
    )
    d = build_risk_decision(_opp(), result, now=_NOW)
    assert d.approved is False
    assert d.violation_rules == ["per_trade_size_limit"]


def test_directional_strategy_flagged() -> None:
    d = build_risk_decision(_opp(StrategyType.DIRECTIONAL),
                            EvaluateResponse(approved=True), now=_NOW)
    assert d.directional is True


def test_advisory_annotation_recorded_when_present() -> None:
    ranking = {"recommended_action": "execute", "profitability_probability": 0.72,
               "model_version": "ranker-v1"}
    d = build_risk_decision(_opp(), EvaluateResponse(approved=True), ranking=ranking, now=_NOW)
    assert d.advisory_action == "execute"
    assert d.advisory_probability == 0.72
    assert d.model_version == "ranker-v1"


def test_advisory_fields_none_when_ranking_absent() -> None:
    d = build_risk_decision(_opp(), EvaluateResponse(approved=True), ranking=None, now=_NOW)
    assert d.advisory_action is None
    assert d.advisory_probability is None
    assert d.model_version is None
