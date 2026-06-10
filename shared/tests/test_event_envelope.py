"""Canonical event envelope (docs/07) + the opt-in publish_event path."""

from __future__ import annotations

from datetime import datetime, timezone

from shared.models.event_envelope import EventEnvelope
from shared.models.opportunity import Opportunity, StrategyType
from shared.pubsub.publisher import NullPublisher, Topic


def _opp() -> Opportunity:
    return Opportunity(
        id="o-env",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="hyperliquid",
        gross_spread_bps=120.0,
        trading_fees_bps=31.0,
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=12.0,
        net_edge_bps=75.0,
        confidence_score=0.85,
        recommended_size_usd=1_000.0,
        min_hold_hours=6.0,
        detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_wrap_from_pydantic_payload_carries_fields_and_defaults():
    env = EventEnvelope.wrap(
        event_type="opportunity.created", payload=_opp(), producer="opportunity-engine"
    )
    assert env.event_type == "opportunity.created"
    assert env.producer == "opportunity-engine"
    assert env.version == 1
    assert env.tenant_id is None
    # auto-generated ids + timestamp
    assert env.event_id.startswith("evt_")
    assert env.correlation_id.startswith("corr_")
    assert env.occurred_at.tzinfo is not None
    # the domain object is preserved under payload, JSON-mode (id round-trips)
    assert env.payload["id"] == "o-env"
    assert env.payload["strategy"] == "funding_rate_arb"


def test_wrap_preserves_explicit_correlation_and_tenant():
    env = EventEnvelope.wrap(
        event_type="payout.completed",
        payload={"amount": 100},
        producer="offramp-orchestrator",
        tenant_id="ten_123",
        correlation_id="corr_fixed",
        version=2,
    )
    assert env.tenant_id == "ten_123"
    assert env.correlation_id == "corr_fixed"
    assert env.version == 2
    assert env.payload == {"amount": 100}


def test_unique_ids_per_event():
    a = EventEnvelope.wrap(event_type="x", payload={}, producer="p")
    b = EventEnvelope.wrap(event_type="x", payload={}, producer="p")
    assert a.event_id != b.event_id
    assert a.correlation_id != b.correlation_id


def test_attributes_are_all_strings_and_omit_absent_tenant():
    env = EventEnvelope.wrap(event_type="signal.created", payload={}, producer="signal-engine")
    attrs = env.attributes()
    assert all(isinstance(v, str) for v in attrs.values())
    assert attrs["event_type"] == "signal.created"
    assert attrs["producer"] == "signal-engine"
    assert attrs["version"] == "1"
    assert "tenant" not in attrs  # tenant_id was None → omitted, not "None"


def test_attributes_include_tenant_when_present():
    env = EventEnvelope.wrap(
        event_type="risk.decided", payload={}, producer="risk-engine", tenant_id="ten_9"
    )
    assert env.attributes()["tenant"] == "ten_9"


def test_envelope_json_roundtrips():
    env = EventEnvelope.wrap(
        event_type="opportunity.created", payload=_opp(), producer="opportunity-engine"
    )
    restored = EventEnvelope.model_validate_json(env.model_dump_json())
    assert restored.event_id == env.event_id
    assert restored.payload["id"] == "o-env"
    # a consumer can re-validate the inner payload back into its concrete model
    assert Opportunity.model_validate(restored.payload).net_edge_bps == 75.0


def test_null_publisher_publish_event_sends_envelope_and_attributes():
    captured: list[tuple] = []

    class _Capture(NullPublisher):
        def publish(self, topic, payload, attributes=None):
            captured.append((topic, payload, attributes))
            return "msg-1"

    mid = _Capture().publish_event(
        Topic.OPPORTUNITIES,
        "opportunity.created",
        _opp(),
        producer="opportunity-engine",
        tenant_id="ten_1",
    )
    assert mid == "msg-1"
    topic, payload, attrs = captured[0]
    assert topic is Topic.OPPORTUNITIES
    assert isinstance(payload, EventEnvelope)
    assert payload.event_type == "opportunity.created"
    assert payload.payload["id"] == "o-env"
    assert attrs["event_type"] == "opportunity.created"
    assert attrs["tenant"] == "ten_1"
    assert attrs["correlation_id"] == payload.correlation_id
