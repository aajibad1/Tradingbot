"""Tests for the Pub/Sub publisher factory + Topic enum (the topic registry)."""

from datetime import datetime

import pytest

from shared.models.opportunity import Opportunity, StrategyType
from shared.pubsub.publisher import NullPublisher, Topic, get_publisher


def _opp() -> Opportunity:
    return Opportunity(
        id="o1", strategy=StrategyType.CROSS_EXCHANGE, asset="BTC",
        long_exchange="kraken", short_exchange="crypto.com",
        gross_spread_bps=1.0, trading_fees_bps=1.0, slippage_estimate_bps=1.0,
        net_edge_bps=1.0, confidence_score=0.5, recommended_size_usd=10_000.0,
        min_hold_hours=1.0, detected_at=datetime(2026, 1, 1),
    )


def test_topic_values_are_stable() -> None:
    # The topic strings are the cross-service contract and must match infra.
    assert Topic.MARKET_DATA.value == "arb-market-data"
    assert Topic.FUNDING_RATES.value == "arb-funding-rates"
    assert Topic.OPPORTUNITIES.value == "arb-opportunities"
    assert Topic.RISK_ALERTS.value == "arb-risk-alerts"
    assert Topic.TRADE_FILLS.value == "arb-trade-fills"
    assert Topic.AI_PROPOSALS.value == "arb-ai-proposals"
    assert Topic.AUDIT_LOG.value == "arb-audit-log"
    assert Topic.SENTIMENT_EVENTS.value == "arb-sentiment-events"


def test_get_publisher_returns_null_without_gcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    pub = get_publisher()
    assert isinstance(pub, NullPublisher)


def test_null_publisher_publish_and_audit() -> None:
    pub = NullPublisher()
    assert pub.publish(Topic.OPPORTUNITIES, _opp(), {"k": "v"}) == "null-message-id"
    assert pub.publish(Topic.OPPORTUNITIES, _opp()) == "null-message-id"
    assert pub.publish_audit("risk-engine", "trade_approved", _opp()) == "null-message-id"


def test_null_publisher_publish_nowait_is_fire_and_forget() -> None:
    pub = NullPublisher()
    # Fire-and-forget returns None (no message_id) and must not raise.
    assert pub.publish_nowait(Topic.MARKET_DATA, _opp(), {"exchange": "kraken"}) is None
    assert pub.publish_nowait(Topic.MARKET_DATA, _opp()) is None
