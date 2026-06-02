"""PROPOSE-tier tools.

A proposal does NOT mutate state. It publishes a structured event to
`arb-ai-proposals`, where a human gates it via Slack. Every proposal is
ALSO mirrored to `arb-audit-log` so we have a tamper-evident record of
what the AI tried to do — independent of whether the proposal is later
approved, rejected, or expires.

The execution path that picks up an approval lives in
execution-orchestrator's approval gate.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from permissions import ToolBlockedError
from shared.pubsub.publisher import Topic, get_publisher

logger = logging.getLogger(__name__)


# Risk-engine limit keys an AI agent may PROPOSE to change. Mirrors the mutable
# entries of DEFAULT_LIMITS in services/risk-engine/rules/drawdown_guard.py.
# Leverage and any withdrawal-adjacent control are DELIBERATELY excluded — a
# leverage increase is a NEVER-tier action (see permissions.py) and must not be
# reachable by laundering it through a risk-limit-change proposal. Validating the
# rule at proposal time is defence-in-depth; the human Slack gate is the primary
# control but must not be the ONLY one.
_PROPOSABLE_RISK_RULES: dict[str, float] = {
    # rule name -> hard upper bound a proposal may request (sanity ceiling)
    "max_daily_loss_pct": 10.0,
    "max_per_trade_capital_pct": 10.0,
    "max_exchange_exposure_pct": 50.0,
    "max_single_asset_exposure_pct": 50.0,
    "min_net_edge_bps": 1000.0,
    "max_api_latency_ms": 5000.0,
}


class Proposal(BaseModel):
    proposal_id: str
    type: str
    payload: dict[str, Any]
    rationale: str
    proposed_by: str = Field(default="ai-ops-agent")
    proposed_at: datetime
    slack_approval_required: bool = True


def _publish(proposal: Proposal) -> dict[str, str]:
    """Publish to AI_PROPOSALS AND AUDIT_LOG. Both must succeed.

    The audit publish is intentionally not wrapped in try/except — if we
    cannot record the proposal in the audit log, we want to fail loud so
    the operator sees that the audit trail broke.
    """
    publisher = get_publisher()
    publisher.publish(
        Topic.AI_PROPOSALS,
        proposal,
        attributes={
            "type": proposal.type,
            "proposal_id": proposal.proposal_id,
            "approval_required": "slack",
        },
    )
    # Mirror to audit log — every proposal must leave a record.
    publisher.publish(
        Topic.AUDIT_LOG,
        proposal,
        attributes={
            "source": "ai-ops-agent",
            "event": f"proposal.{proposal.type}",
            "proposal_id": proposal.proposal_id,
        },
    )
    logger.info(
        "ai-ops proposal published id=%s type=%s",
        proposal.proposal_id,
        proposal.type,
    )
    return {
        "proposal_id": proposal.proposal_id,
        "status": "queued_for_human_approval",
        "approval_channel": "slack",
    }


def propose_risk_limit_change(rule: str, new_value: float, rationale: str) -> dict[str, str]:
    """Propose a change to a single risk-engine limit (e.g. max_daily_loss_pct).

    Only the allowlisted mutable limits may be proposed. Leverage- and
    withdrawal-adjacent rules are blocked here (not merely at the downstream
    approval gate) so a NEVER-tier action cannot be smuggled through this tool.
    """
    rule_norm = rule.strip().lower()
    max_allowed = _PROPOSABLE_RISK_RULES.get(rule_norm)
    if max_allowed is None:
        raise ToolBlockedError(
            f"risk rule {rule!r} is not proposable by an AI agent "
            f"(allowed: {sorted(_PROPOSABLE_RISK_RULES)}); "
            "leverage / withdrawal changes are NEVER-tier"
        )
    if not (0 < new_value <= max_allowed):
        raise ValueError(
            f"new_value {new_value} out of bounds for {rule_norm} — must be in (0, {max_allowed}]"
        )
    proposal = Proposal(
        proposal_id=str(uuid.uuid4()),
        type="risk_limit_change",
        payload={"rule": rule_norm, "new_value": new_value},
        rationale=rationale,
        proposed_at=datetime.now(timezone.utc),
    )
    return _publish(proposal)


def propose_size_adjustment(new_size_usd: float, rationale: str) -> dict[str, str]:
    """Propose a new per-trade size in USD."""
    proposal = Proposal(
        proposal_id=str(uuid.uuid4()),
        type="size_adjustment",
        payload={"new_size_usd": new_size_usd},
        rationale=rationale,
        proposed_at=datetime.now(timezone.utc),
    )
    return _publish(proposal)


def propose_strategy_pause(strategy: str, rationale: str, duration_minutes: int = 60) -> dict[str, str]:
    """Propose pausing a specific strategy for a duration (default 1h)."""
    proposal = Proposal(
        proposal_id=str(uuid.uuid4()),
        type="strategy_pause",
        payload={"strategy": strategy, "duration_minutes": duration_minutes},
        rationale=rationale,
        proposed_at=datetime.now(timezone.utc),
    )
    return _publish(proposal)
