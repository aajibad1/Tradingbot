"""Propose tools should NEVER mutate state — they only publish events.

Every proposal must publish to BOTH:
  - Topic.AI_PROPOSALS  → consumed by the Slack approval bot
  - Topic.AUDIT_LOG     → permanent record of what the AI tried to do
"""

from unittest.mock import patch

from shared.pubsub.publisher import Topic
from tools.propose_tools import (
    propose_risk_limit_change,
    propose_size_adjustment,
    propose_strategy_pause,
)


def _topics_published(publisher_mock) -> list[Topic]:
    return [call.args[0] for call in publisher_mock.publish.call_args_list]


def test_propose_risk_limit_change_publishes_event() -> None:
    with patch("tools.propose_tools.get_publisher") as gp:
        publisher = gp.return_value
        result = propose_risk_limit_change("max_daily_loss_pct", 1.5, "needed more headroom")
        # 2 calls: AI_PROPOSALS + AUDIT_LOG
        assert publisher.publish.call_count == 2
        proposals_call, audit_call = publisher.publish.call_args_list
        topic, payload, *_ = proposals_call.args
        assert topic == Topic.AI_PROPOSALS
        assert payload.type == "risk_limit_change"
        assert payload.payload["rule"] == "max_daily_loss_pct"
        assert payload.payload["new_value"] == 1.5
        assert payload.slack_approval_required is True
        assert result["status"] == "queued_for_human_approval"
        assert result["approval_channel"] == "slack"
        # Audit-log mirror
        assert audit_call.args[0] == Topic.AUDIT_LOG
        assert audit_call.args[1].proposal_id == payload.proposal_id


def test_propose_size_adjustment_publishes_event() -> None:
    with patch("tools.propose_tools.get_publisher") as gp:
        publisher = gp.return_value
        propose_size_adjustment(15_000.0, "vol increased")
        topics = _topics_published(publisher)
        assert Topic.AI_PROPOSALS in topics
        assert Topic.AUDIT_LOG in topics
        payload = publisher.publish.call_args_list[0].args[1]
        assert payload.type == "size_adjustment"
        assert payload.payload["new_size_usd"] == 15_000.0


def test_propose_strategy_pause_publishes_event() -> None:
    with patch("tools.propose_tools.get_publisher") as gp:
        publisher = gp.return_value
        propose_strategy_pause("cross_exchange", "spreads too tight", duration_minutes=120)
        topics = _topics_published(publisher)
        assert Topic.AI_PROPOSALS in topics
        assert Topic.AUDIT_LOG in topics
        payload = publisher.publish.call_args_list[0].args[1]
        assert payload.type == "strategy_pause"
        assert payload.payload == {"strategy": "cross_exchange", "duration_minutes": 120}


def test_every_propose_call_writes_to_audit_log() -> None:
    """The audit invariant: no proposal exists without an audit-log entry."""
    with patch("tools.propose_tools.get_publisher") as gp:
        publisher = gp.return_value
        propose_risk_limit_change("max_drawdown_pct", 5.0, "r1")
        propose_size_adjustment(5_000.0, "r2")
        propose_strategy_pause("funding_capture", "r3")
        topics = _topics_published(publisher)
        # 3 proposals → 3 AI_PROPOSALS + 3 AUDIT_LOG = 6 publishes
        assert topics.count(Topic.AI_PROPOSALS) == 3
        assert topics.count(Topic.AUDIT_LOG) == 3


def test_audit_log_attributes_identify_source() -> None:
    """The audit publisher needs source + event tags to be searchable."""
    with patch("tools.propose_tools.get_publisher") as gp:
        publisher = gp.return_value
        propose_risk_limit_change("max_daily_loss_pct", 2.0, "test")
        audit_call = publisher.publish.call_args_list[1]
        attrs = audit_call.kwargs.get("attributes") or (
            audit_call.args[2] if len(audit_call.args) > 2 else {}
        )
        assert attrs.get("source") == "ai-ops-agent"
        assert attrs.get("event", "").startswith("proposal.")
        assert "proposal_id" in attrs
