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

from shared.pubsub.publisher import Topic, get_publisher

logger = logging.getLogger(__name__)


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
    """Propose a change to a single risk-engine limit (e.g. max_daily_loss_pct)."""
    proposal = Proposal(
        proposal_id=str(uuid.uuid4()),
        type="risk_limit_change",
        payload={"rule": rule, "new_value": new_value},
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
