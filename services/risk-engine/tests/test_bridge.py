"""Approve→execute bridge: risk-engine forwards only APPROVED opportunities,
marked execute=True, to APPROVED_OPPORTUNITIES."""

from __future__ import annotations

from datetime import datetime


from shared.models.opportunity import Opportunity, StrategyType
from shared.pubsub.publisher import Topic


def _opp(net_edge_bps: float = 75.0, size_usd: float = 1_000.0) -> Opportunity:
    return Opportunity(
        id="o-bridge",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="hyperliquid",
        gross_spread_bps=120.0,
        trading_fees_bps=31.0,
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=12.0,
        net_edge_bps=net_edge_bps,
        confidence_score=0.85,
        recommended_size_usd=size_usd,
        min_hold_hours=6.0,
        detected_at=datetime(2026, 1, 1),
    )


def _seed_health(fake_redis, latency_ms: float = 50.0) -> None:
    fake_redis.set("health:latency:kraken", str(latency_ms))
    fake_redis.set("health:latency:hyperliquid", str(latency_ms))


class _CapturePublisher:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, attributes=None):
        self.published.append((topic, payload, attributes))
        return "msg-id"


class _FakeMessage:
    def __init__(self, payload: Opportunity):
        self.data = payload.model_dump_json().encode()
        self.acked = False
        self.nacked = False

    def ack(self):
        self.acked = True

    def nack(self):
        self.nacked = True


def test_approved_opportunity_is_forwarded_with_execute_true(fake_redis, monkeypatch):
    _seed_health(fake_redis)
    import main
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "_get_publisher", lambda: pub)

    msg = _FakeMessage(_opp())
    main._on_opportunity(msg)

    assert msg.acked
    approved = [p for p in pub.published if p[0] == Topic.APPROVED_OPPORTUNITIES]
    assert len(approved) == 1
    _, payload, _attrs = approved[0]
    assert payload.execute is True
    assert payload.id == "o-bridge"

    # AI Phase A: an approved opportunity also emits a RiskDecision fact.
    decisions = [p for p in pub.published if p[0] == Topic.RISK_DECISIONS]
    assert len(decisions) == 1
    assert decisions[0][1].approved is True
    assert decisions[0][1].opportunity_id == "o-bridge"


def test_rejected_opportunity_is_not_forwarded(fake_redis, monkeypatch):
    _seed_health(fake_redis)
    import main
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "_get_publisher", lambda: pub)

    # 10% of capital ($10k on $100k) blows the per-trade size limit → rejected.
    msg = _FakeMessage(_opp(size_usd=10_000.0))
    main._on_opportunity(msg)

    assert msg.acked              # acked (handled), but nothing forwarded for execution
    assert [p for p in pub.published if p[0] == Topic.APPROVED_OPPORTUNITIES] == []

    # AI Phase A: rejects are still captured as facts (the negatives ML needs).
    decisions = [p for p in pub.published if p[0] == Topic.RISK_DECISIONS]
    assert len(decisions) == 1
    decision = decisions[0][1]
    assert decision.approved is False
    assert decision.violation_rules  # at least the per-trade size limit fired


def test_evaluate_and_forward_returns_none_when_rejected(fake_redis):
    _seed_health(fake_redis)
    import main
    assert main.evaluate_and_forward(_opp(net_edge_bps=10.0)) is None  # below 50bp bar


def test_evaluate_and_forward_marks_execute_when_approved(fake_redis):
    _seed_health(fake_redis)
    import main
    out = main.evaluate_and_forward(_opp())
    assert out is not None and out.execute is True


def test_advisory_rank_annotates_published_message(fake_redis, monkeypatch):
    _seed_health(fake_redis)
    import main
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "_get_publisher", lambda: pub)
    monkeypatch.setattr(main, "_advisory_rank",
                        lambda opp: {"recommended_action": "continue_to_risk",
                                     "profitability_probability": 0.82})
    main._on_opportunity(_FakeMessage(_opp()))
    _, _, attrs = pub.published[0]
    assert attrs["rank_action"] == "continue_to_risk"
    assert attrs["rank_prob"] == "0.82"


def test_advisory_rank_failure_is_fail_open(fake_redis, monkeypatch):
    _seed_health(fake_redis)
    import main
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "_get_publisher", lambda: pub)
    # ranker "down" → _advisory_rank returns None; approval still publishes.
    monkeypatch.setattr(main, "_advisory_rank", lambda opp: None)
    msg = _FakeMessage(_opp())
    main._on_opportunity(msg)
    assert msg.acked
    approved = [p for p in pub.published if p[0] == Topic.APPROVED_OPPORTUNITIES]
    assert len(approved) == 1
    assert "rank_action" not in approved[0][2]
