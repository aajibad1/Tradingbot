"""Slack-gated human approval for WRITE operations.

Flow:
  1. Opportunity arrives (risk-approved, execute=True).
  2. We persist it to Redis at `approval:pending:<opportunity_id>` with TTL.
  3. We POST an interactive Slack message with Approve / Reject buttons.
  4. Slack's interaction webhook hits POST /slack/interaction here:
     - On approve  → mark approved in Redis, forward to Hummingbot.
     - On reject   → mark rejected in Redis, emit audit event.
  5. Expiration: pending opportunities older than `APPROVAL_TIMEOUT_S` are
     auto-rejected during the periodic sweep.

We do not block waiting on a button click — Cloud Run requests have hard
limits. Approval state is persisted in Redis and the orchestrator drains
approved items on a tick loop.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from shared.models.opportunity import Opportunity

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


KEY_PREFIX_PENDING = "approval:pending:"
KEY_PREFIX_APPROVED = "approval:approved:"
KEY_PREFIX_REJECTED = "approval:rejected:"
DEFAULT_APPROVAL_TIMEOUT_S = 600  # 10 minutes

# Risk-engine owns this key — see services/risk-engine/state.py for the Redis
# contract. The orchestrator is a read-only consumer: a tripped kill switch
# must halt the live execution path (CLAUDE.md: "risk-engine is the choke point").
KEY_KILL_SWITCH_ACTIVE = "risk:kill_switch:active"


def kill_switch_active(redis: Redis) -> bool:
    """True iff the risk-engine kill switch is engaged.

    Fails SAFE: if Redis is unreachable or returns an unexpected value we treat
    the switch as active, so an unavailable risk-state store can never let live
    orders slip through the gate.
    """
    try:
        return redis.get(KEY_KILL_SWITCH_ACTIVE) == "1"
    except Exception:  # noqa: BLE001 — money path must fail closed
        logger.exception("kill-switch check failed — failing safe (treating as ACTIVE)")
        return True


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PendingApproval(BaseModel):
    opportunity_id: str
    opportunity: Opportunity
    posted_at: datetime
    expires_at: datetime
    slack_ts: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    decided_at: datetime | None = None


def _redis() -> Redis:
    from redis import Redis

    return Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


DEFAULT_SUPERVISED_DAYS = 14


def approval_required(now: datetime | None = None) -> bool:
    """Whether a trade needs human Slack approval before it can execute.

    Implements the spec's "human confirmation for the first N days of live
    trading" — but FAILS SAFE: approval is ALWAYS required unless an operator
    has *explicitly* opted into autonomous execution AND the supervised window
    has elapsed. Autonomous live trading is never a silent default.

    Config:
      ENABLE_AUTONOMOUS_EXECUTION = "true" to ever allow auto-execution.
      LIVE_SINCE                  = ISO date/datetime when live trading began.
      APPROVAL_REQUIRED_DAYS      = supervised-window length (default 14).
    """
    if os.environ.get("ENABLE_AUTONOMOUS_EXECUTION", "false").lower() != "true":
        return True  # default posture: every trade is human-approved
    live_since = os.environ.get("LIVE_SINCE")
    if not live_since:
        return True  # autonomous requested but no go-live date recorded → stay safe
    try:
        started = datetime.fromisoformat(live_since)
    except ValueError:
        logger.error("LIVE_SINCE=%r is not ISO format — requiring approval", live_since)
        return True
    try:
        days = int(os.environ.get("APPROVAL_REQUIRED_DAYS", str(DEFAULT_SUPERVISED_DAYS)))
    except ValueError:
        days = DEFAULT_SUPERVISED_DAYS
    now = now or datetime.utcnow()
    return (now - started) < timedelta(days=days)  # True while still in the supervised window


def auto_approve(opp: Opportunity) -> PendingApproval:
    """Persist an opportunity directly as APPROVED, bypassing the Slack gate.

    Used ONLY in autonomous mode (after the supervised window). The orchestrator
    tick loop drains the approved set exactly as it would for a human approval.
    """
    now = datetime.utcnow()
    pending = PendingApproval(
        opportunity_id=opp.id,
        opportunity=opp,
        posted_at=now,
        expires_at=now + timedelta(hours=1),
        status=ApprovalStatus.APPROVED,
        decided_by="auto-execution",
        decided_at=now,
    )
    _redis().setex(f"{KEY_PREFIX_APPROVED}{opp.id}", 3600, pending.model_dump_json())
    logger.warning("AUTONOMOUS execution: opp=%s auto-approved (past supervised window)", opp.id)
    return pending


def submit_for_approval(opp: Opportunity) -> PendingApproval:
    """Persist a pending approval and post the Slack prompt."""
    timeout_s = int(os.environ.get("APPROVAL_TIMEOUT_S", str(DEFAULT_APPROVAL_TIMEOUT_S)))
    now = datetime.utcnow()
    pending = PendingApproval(
        opportunity_id=opp.id,
        opportunity=opp,
        posted_at=now,
        expires_at=datetime.fromtimestamp(now.timestamp() + timeout_s),
    )

    redis = _redis()
    redis.setex(f"{KEY_PREFIX_PENDING}{opp.id}", timeout_s, pending.model_dump_json())

    ts = _post_slack_approval_prompt(pending)
    if ts:
        pending.slack_ts = ts
        redis.setex(f"{KEY_PREFIX_PENDING}{opp.id}", timeout_s, pending.model_dump_json())
    return pending


def decide(opportunity_id: str, status: ApprovalStatus, decided_by: str) -> PendingApproval | None:
    """Process a Slack button click. Idempotent — repeat clicks are no-ops."""
    redis = _redis()
    raw = redis.get(f"{KEY_PREFIX_PENDING}{opportunity_id}")
    if not raw:
        logger.warning("approval decision on unknown/expired id=%s status=%s", opportunity_id, status.value)
        return None

    pending = PendingApproval.model_validate_json(raw)
    if pending.status is not ApprovalStatus.PENDING:
        return pending  # already decided

    pending.status = status
    pending.decided_by = decided_by
    pending.decided_at = datetime.utcnow()

    redis.delete(f"{KEY_PREFIX_PENDING}{opportunity_id}")
    if status is ApprovalStatus.APPROVED:
        redis.setex(f"{KEY_PREFIX_APPROVED}{opportunity_id}", 3600, pending.model_dump_json())
    elif status is ApprovalStatus.REJECTED:
        redis.setex(f"{KEY_PREFIX_REJECTED}{opportunity_id}", 86400, pending.model_dump_json())

    logger.info(
        "approval decided id=%s status=%s by=%s", opportunity_id, status.value, decided_by
    )
    return pending


def drain_approved(limit: int = 50) -> list[PendingApproval]:
    """Pop approved opportunities for execution. Removes them from the approved set.

    Each key is claimed with an atomic ``GETDEL`` rather than a separate
    get-then-delete. Two concurrent ticks (overlapping cron pings, or a cron
    tick racing a manual call) can therefore never both read the same approved
    opportunity and submit it twice — the loser of the race gets ``None`` from
    ``getdel`` and skips it. ``GETDEL`` requires Redis >= 6.2 (Memorystore ok).
    """
    redis = _redis()
    out: list[PendingApproval] = []
    for key in redis.scan_iter(match=f"{KEY_PREFIX_APPROVED}*"):
        raw = redis.getdel(key)  # atomic claim — closes the double-execution window
        if not raw:
            continue
        out.append(PendingApproval.model_validate_json(raw))
        if len(out) >= limit:
            break
    return out


def sweep_expired() -> int:
    """Mark any pending approvals past their expiry as EXPIRED. Returns count."""
    redis = _redis()
    expired = 0
    now = time.time()
    for key in redis.scan_iter(match=f"{KEY_PREFIX_PENDING}*"):
        raw = redis.get(key)
        if not raw:
            continue
        pending = PendingApproval.model_validate_json(raw)
        if pending.expires_at.timestamp() < now:
            pending.status = ApprovalStatus.EXPIRED
            pending.decided_at = datetime.utcnow()
            redis.delete(key)
            expired += 1
            logger.warning("approval expired id=%s posted_at=%s", pending.opportunity_id, pending.posted_at)
    return expired


# ── Slack glue ─────────────────────────────────────────────────────────────


def _slack_token() -> str | None:
    return os.environ.get("SLACK_BOT_TOKEN")


def _slack_channel() -> str:
    return os.environ.get("SLACK_APPROVAL_CHANNEL", "#trading-approvals")


def _post_slack_approval_prompt(pending: PendingApproval) -> str | None:
    token = _slack_token()
    if not token:
        logger.info("SLACK_BOT_TOKEN unset — skipping slack post (id=%s)", pending.opportunity_id)
        return None

    opp = pending.opportunity
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Trade approval requested*\n"
                    f"`{opp.strategy.value}` on `{opp.asset}` — "
                    f"long `{opp.long_exchange}` / short `{opp.short_exchange}`\n"
                    f"Net edge: `{opp.net_edge_bps:.1f} bps`  •  "
                    f"Size: `${opp.recommended_size_usd:,.0f}`  •  "
                    f"Confidence: `{opp.confidence_score:.2f}`\n"
                    f"ID: `{opp.id}`  •  Expires <!date^{int(pending.expires_at.timestamp())}^{{time}}|soon>"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "value": f"approve:{opp.id}",
                    "action_id": "approve_opportunity",
                },
                {
                    "type": "button",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "value": f"reject:{opp.id}",
                    "action_id": "reject_opportunity",
                },
            ],
        },
    ]
    response = httpx.post(
        "https://slack.com/api/chat.postMessage",
        json={"channel": _slack_channel(), "blocks": blocks, "text": "Trade approval requested"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        logger.error("slack post failed: %s", payload)
        return None
    return payload.get("ts")


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify Slack's request signature. Reject anything older than 5 min."""
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts_int) > 300:
        return False
    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
    digest = hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)
