"""Propose tools should NEVER mutate state — they only publish events.

Every proposal must publish to BOTH:
  - Topic.AI_PROPOSALS  → consumed by the Slack approval bot
  - Topic.AUDIT_LOG     → permanent record of what the AI tried to do
"""

from unittest.mock import patch

import pytest

from permissions import ToolBlockedError
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
        # Audit-log mirror — a canonical AuditLogEntry, not the raw Proposal
        # (trade-ledger validates every Topic.AUDIT_LOG message against it).
        assert audit_call.args[0] == Topic.AUDIT_LOG
        entry = audit_call.args[1]
        assert entry.source == "ai-ops-agent"
        assert entry.resource_id == payload.proposal_id
        assert entry.event_type == f"proposal.{payload.type}"


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
        propose_risk_limit_change("max_daily_loss_pct", 5.0, "r1")
        propose_size_adjustment(5_000.0, "r2")
        propose_strategy_pause("funding_capture", "r3")
        topics = _topics_published(publisher)
        # 3 proposals → 3 AI_PROPOSALS + 3 AUDIT_LOG = 6 publishes
        assert topics.count(Topic.AI_PROPOSALS) == 3
        assert topics.count(Topic.AUDIT_LOG) == 3


def test_leverage_change_cannot_be_laundered_through_a_proposal() -> None:
    """M5: a leverage increase is NEVER-tier — it must be blocked at proposal
    time, not merely at the downstream approval gate. No event is published."""
    with patch("tools.propose_tools.get_publisher") as gp:
        for rule in ("max_leverage_multiplier", "leverage_limit", "increase_leverage"):
            with pytest.raises(ToolBlockedError):
                propose_risk_limit_change(rule, 5.0, "sneaky")
        gp.return_value.publish.assert_not_called()


def test_risk_limit_proposal_rejects_out_of_bounds_value() -> None:
    with patch("tools.propose_tools.get_publisher") as gp:
        with pytest.raises(ValueError):
            propose_risk_limit_change("max_daily_loss_pct", 999.0, "way too loose")
        with pytest.raises(ValueError):
            propose_risk_limit_change("max_daily_loss_pct", -1.0, "negative")
        gp.return_value.publish.assert_not_called()


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


# --- approval-gate-service registration (opt-in via APPROVAL_GATE_URL) ------ #


def test_approval_gate_not_contacted_when_url_unset(monkeypatch) -> None:
    """Default (no APPROVAL_GATE_URL): no A2A call is attempted at all — the
    Slack/Pub/Sub flow above is unaffected and this stays a pure no-op."""
    monkeypatch.delenv("APPROVAL_GATE_URL", raising=False)
    with patch("tools.propose_tools.get_publisher"), \
         patch("shared.a2a.A2AClient") as client_cls:
        propose_size_adjustment(5_000.0, "no gate configured")
        client_cls.assert_not_called()


def test_approval_gate_registered_when_url_set(monkeypatch) -> None:
    monkeypatch.setenv("APPROVAL_GATE_URL", "http://approval-gate.local")
    with patch("tools.propose_tools.get_publisher") as gp, \
         patch("shared.a2a.A2AClient") as client_cls:
        instance = client_cls.return_value
        result = propose_strategy_pause("cross_exchange", "spreads too tight")

        client_cls.assert_called_once()
        assert client_cls.call_args.args[0] == "http://approval-gate.local"
        instance.send_data.assert_called_once()
        sent = instance.send_data.call_args.args[0]
        assert sent["action_type"] == "strategy_pause"
        assert sent["payload"]["strategy"] == "cross_exchange"
        assert sent["payload"]["ai_ops_proposal_id"] == result["proposal_id"]
        # The primary Slack flow is unaffected by this best-effort side channel.
        assert gp.return_value.publish.call_count == 2


def test_approval_gate_registration_is_fail_soft(monkeypatch) -> None:
    """An approval-gate-service outage must never break the primary proposal
    flow — the Slack/audit publishes still succeed and the caller still gets
    a normal response."""
    monkeypatch.setenv("APPROVAL_GATE_URL", "http://approval-gate.local")
    with patch("tools.propose_tools.get_publisher") as gp, \
         patch("shared.a2a.A2AClient") as client_cls:
        client_cls.return_value.send_data.side_effect = ConnectionError("unreachable")
        result = propose_size_adjustment(2_500.0, "gate is down")

        assert result["status"] == "queued_for_human_approval"
        assert gp.return_value.publish.call_count == 2
