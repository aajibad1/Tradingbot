"""Permissions are the security backbone — test them thoroughly."""

import pytest

from permissions import PermissionLevel, ToolBlockedError, require


def test_read_tools_return_read_level() -> None:
    for name in [
        "get_balances",
        "get_daily_pnl",
        "get_open_positions",
        "get_exchange_health",
        "get_opportunity_feed",
        "generate_daily_report",
        "detect_anomalies",
    ]:
        assert require(name) == PermissionLevel.READ


def test_propose_tools_return_propose_level() -> None:
    for name in [
        "propose_risk_limit_change",
        "propose_size_adjustment",
        "propose_strategy_pause",
    ]:
        assert require(name) == PermissionLevel.PROPOSE


def test_never_tools_are_blocked() -> None:
    for name in [
        "withdraw_funds",
        "increase_leverage",
        "add_exchange_key",
        "disable_kill_switch",
    ]:
        with pytest.raises(ToolBlockedError):
            require(name)


def test_unregistered_tool_fails_closed() -> None:
    with pytest.raises(ToolBlockedError):
        require("mystery_tool_we_never_registered")


def test_dispatcher_refuses_never_tier() -> None:
    """End-to-end: even if a tool name exists in some impl map, NEVER blocks it."""
    from mcp_server import call_tool

    with pytest.raises(ToolBlockedError):
        call_tool("withdraw_funds")
    with pytest.raises(ToolBlockedError):
        call_tool("disable_kill_switch")
    with pytest.raises(ToolBlockedError):
        call_tool("increase_leverage")
