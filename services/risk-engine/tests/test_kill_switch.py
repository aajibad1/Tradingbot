"""Tests for the kill switch path.

Covers:
  - Triggering trips Redis ``risk:kill_switch:active``.
  - Trigger is idempotent (no double-alert).
  - Reset requires the correct ``KILL_SWITCH_RESET_TOKEN``.
  - Reset rejects wrong / missing tokens.
  - ``RuntimeError`` when the reset env-var is unset entirely.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rules.kill_switch import (
    clear_kill_switch,
    is_kill_switch_active,
    trigger_kill_switch,
)
from shared.pubsub.publisher import Topic


def test_trigger_sets_redis_flag(fake_redis) -> None:
    assert is_kill_switch_active(fake_redis) is False
    state = trigger_kill_switch(fake_redis, triggered_by="unit-test", reason="manual")
    assert state.active is True
    assert state.reason == "manual"
    assert state.triggered_by == "unit-test"
    assert is_kill_switch_active(fake_redis) is True
    # The exact Redis layout is part of the cross-service contract.
    assert fake_redis.get("risk:kill_switch:active") == "1"


def test_trigger_is_idempotent(fake_redis) -> None:
    first = trigger_kill_switch(fake_redis, "x", "first")
    second = trigger_kill_switch(fake_redis, "y", "second")
    # The second trigger must not overwrite the original metadata.
    assert second.reason == first.reason == "first"
    assert second.triggered_by == "x"


def test_reset_requires_correct_token(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("KILL_SWITCH_RESET_TOKEN", "the-right-token")
    trigger_kill_switch(fake_redis, "x", "drawdown")

    with pytest.raises(PermissionError):
        clear_kill_switch(fake_redis, auth_token="wrong", reset_by="ops")
    assert is_kill_switch_active(fake_redis) is True


def test_reset_rejects_missing_token(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("KILL_SWITCH_RESET_TOKEN", "the-right-token")
    trigger_kill_switch(fake_redis, "x", "drawdown")

    with pytest.raises(PermissionError):
        clear_kill_switch(fake_redis, auth_token="", reset_by="ops")
    assert is_kill_switch_active(fake_redis) is True


def test_reset_succeeds_with_correct_token(fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("KILL_SWITCH_RESET_TOKEN", "the-right-token")
    trigger_kill_switch(fake_redis, "x", "drawdown")
    state = clear_kill_switch(
        fake_redis, auth_token="the-right-token", reset_by="ops-oncall"
    )
    assert state.active is False
    assert is_kill_switch_active(fake_redis) is False


def test_reset_raises_when_env_var_unset(fake_redis, monkeypatch) -> None:
    monkeypatch.delenv("KILL_SWITCH_RESET_TOKEN", raising=False)
    trigger_kill_switch(fake_redis, "x", "drawdown")

    with pytest.raises(RuntimeError):
        clear_kill_switch(fake_redis, auth_token="anything", reset_by="ops")


def test_trigger_publishes_a_risk_alert_not_the_raw_state(fake_redis) -> None:
    """RiskAlert is the canonical Topic.RISK_ALERTS shape — trade-ledger's
    _on_risk_alert validates every message against it. Publishing the bare
    KillSwitchState (no alert_type/emitted_at) used to raise KeyError on the
    consumer side for every single kill-switch event."""
    with patch("rules.kill_switch.get_publisher") as gp:
        publisher = gp.return_value
        trigger_kill_switch(fake_redis, triggered_by="ops", reason="drawdown breach")

        publisher.publish.assert_called_once()
        topic, alert = publisher.publish.call_args.args
        assert topic is Topic.RISK_ALERTS
        assert alert.alert_type == "kill_switch_activated"
        assert alert.severity == "critical"
        assert "ops" in alert.message and "drawdown breach" in alert.message
        assert alert.source == "risk-engine"
        assert alert.emitted_at is not None
