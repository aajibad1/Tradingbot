"""Kill switch — single source of truth on whether trading is allowed.

Anything downstream of risk-engine MUST check ``is_kill_switch_active()`` before
emitting an order. The kill switch is checked atomically in Redis. Resetting
requires the ``KILL_SWITCH_RESET_TOKEN`` shared secret — there is no UI bypass.

On trigger this module performs five durable side-effects:
  1. Persist ``risk:kill_switch:active = "1"`` (no expiry) in Redis.
  2. Persist ``risk:kill_switch:metadata`` (triggered_by, reason, triggered_at).
  3. Publish a ``KILL_SWITCH_ACTIVATED`` event to ``Topic.RISK_ALERTS``.
  4. Best-effort Slack alert via ``SLACK_WEBHOOK_URL``.
  5. CRITICAL log line for Cloud Logging.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

import state as state_module
from shared.models.risk_state import KillSwitchState
from shared.pubsub.publisher import Topic, get_publisher

if TYPE_CHECKING:
    from redis import Redis


logger = logging.getLogger(__name__)


def is_kill_switch_active(redis: Redis) -> bool:
    """True iff the kill switch is currently engaged."""
    return redis.get(state_module.KEY_KILL_ACTIVE) == "1"


def trigger_kill_switch(redis: Redis, triggered_by: str, reason: str) -> KillSwitchState:
    """Trip the kill switch and fan out alerts.

    Idempotent: if already active, returns the persisted state without
    re-alerting. Slack / Pub/Sub failures are swallowed — the Redis write is
    the contract, side-channels are best-effort.
    """
    if is_kill_switch_active(redis):
        return state_module.load_kill_switch(redis)

    new_state = state_module.set_kill_switch(redis, triggered_by, reason)
    triggered_iso = (
        new_state.triggered_at.isoformat()
        if new_state.triggered_at
        else datetime.utcnow().isoformat()
    )

    # 1. CRITICAL log line — Cloud Logging will alert on severity.
    logger.critical(
        "KILL_SWITCH_ACTIVATED triggered_by=%s reason=%s at=%s",
        triggered_by,
        reason,
        triggered_iso,
    )

    # 2. Pub/Sub fan-out so paper-trader / execution-orchestrator can react.
    _publish_kill_switch_event(new_state)

    # 3. Slack alert (best-effort).
    _send_slack_alert(
        f":rotating_light: *KILL SWITCH ACTIVATED*\n"
        f"By: `{triggered_by}`\n"
        f"Reason: {reason}\n"
        f"At: {triggered_iso}"
    )
    return new_state


def clear_kill_switch(redis: Redis, auth_token: str, reset_by: str) -> KillSwitchState:
    """Reset the kill switch. Requires KILL_SWITCH_RESET_TOKEN to match.

    Raises:
        RuntimeError: if the env-var is unset (mis-configured deployment).
        PermissionError: if the supplied ``auth_token`` does not match.
    """
    expected = os.environ.get("KILL_SWITCH_RESET_TOKEN")
    if not expected:
        raise RuntimeError(
            "KILL_SWITCH_RESET_TOKEN unset — cannot authenticate reset request"
        )
    if not auth_token or auth_token != expected:
        logger.warning(
            "kill_switch reset rejected reset_by=%s reason=bad_or_missing_token",
            reset_by,
        )
        raise PermissionError("invalid auth_token")

    state_module.clear_kill_switch(redis)
    logger.warning("kill_switch reset reset_by=%s", reset_by)
    _send_slack_alert(f":white_check_mark: Kill switch reset by `{reset_by}`")
    return KillSwitchState(active=False)


# ----- internal helpers --------------------------------------------------- #


def _publish_kill_switch_event(state: KillSwitchState) -> None:
    """Publish a KILL_SWITCH_ACTIVATED event to ``Topic.RISK_ALERTS``.

    Errors are logged and swallowed — a Pub/Sub outage must never prevent the
    in-Redis kill switch from being honoured (Redis is the contract).
    """
    try:
        publisher = get_publisher()
        publisher.publish(
            Topic.RISK_ALERTS,
            state,
            attributes={"event": "KILL_SWITCH_ACTIVATED", "source": "risk-engine"},
        )
    except Exception:  # noqa: BLE001 — alert path is best-effort by design
        logger.exception("failed to publish kill-switch event to RISK_ALERTS")


def _send_slack_alert(text: str) -> None:
    """Best-effort Slack webhook ping.

    Reads ``SLACK_WEBHOOK_URL`` (canonical) with a legacy fallback to
    ``SLACK_ALERT_WEBHOOK``. If neither is set we silently skip — that is the
    expected state in local dev.
    """
    webhook = os.environ.get("SLACK_WEBHOOK_URL") or os.environ.get("SLACK_ALERT_WEBHOOK")
    if not webhook:
        logger.info("slack webhook unset — skipping alert: %s", text)
        return
    try:
        httpx.post(webhook, json={"text": text}, timeout=5.0)
    except httpx.HTTPError:
        # Don't let a Slack failure block the kill switch — log and move on.
        logger.exception("failed to send slack alert")
