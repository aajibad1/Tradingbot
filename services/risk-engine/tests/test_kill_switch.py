"""Tests for the kill switch path.

Covers:
  - Triggering trips Redis ``risk:kill_switch:active``.
  - Trigger is idempotent (no double-alert).
  - Reset requires the correct ``KILL_SWITCH_RESET_TOKEN``.
  - Reset rejects wrong / missing tokens.
  - ``RuntimeError`` when the reset env-var is unset entirely.
"""

from __future__ import annotations

import pytest

from rules.kill_switch import (
    clear_kill_switch,
    is_kill_switch_active,
    trigger_kill_switch,
)


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
