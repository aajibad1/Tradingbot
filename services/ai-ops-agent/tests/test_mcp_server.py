"""MCP server invariants — what the LLM can see and call."""

from unittest.mock import patch

import pytest

from mcp_server import _TOOL_IMPLS, call_tool, list_tools
from permissions import TOOL_PERMISSIONS, PermissionLevel, ToolBlockedError


NEVER_TOOLS = [
    "withdraw_funds",
    "increase_leverage",
    "add_exchange_key",
    "disable_kill_switch",
]


def test_list_tools_excludes_never_tier() -> None:
    """NEVER-tier tools MUST NOT be advertised. This is the security boundary."""
    names = [t["name"] for t in list_tools()]
    for blocked in NEVER_TOOLS:
        assert blocked not in names, f"{blocked!r} must NEVER appear in the MCP catalog"


def test_list_tools_has_no_implementation_for_never_tools() -> None:
    """Defence-in-depth: even if list_tools is bypassed, dispatch fails."""
    for blocked in NEVER_TOOLS:
        assert blocked not in _TOOL_IMPLS


def test_list_tools_contains_all_read_and_propose() -> None:
    expected = {
        name for name, lvl in TOOL_PERMISSIONS.items() if lvl is not PermissionLevel.NEVER
    }
    actual = {t["name"] for t in list_tools()}
    assert actual == expected


def test_list_tools_entries_carry_permission_tier() -> None:
    for t in list_tools():
        assert t["permission"] in {"read", "propose"}
        assert "name" in t and "description" in t


@pytest.mark.parametrize("blocked", NEVER_TOOLS)
def test_call_tool_blocks_never_tier(blocked: str) -> None:
    """Every NEVER tool must raise — clear, loud, permanently."""
    with pytest.raises(ToolBlockedError) as exc_info:
        call_tool(blocked)
    assert "permanently blocked" in str(exc_info.value).lower() or "refusing" in str(
        exc_info.value
    ).lower()


def test_call_tool_blocks_unregistered_name() -> None:
    with pytest.raises(ToolBlockedError):
        call_tool("totally_made_up_tool_name")


def test_propose_dispatch_does_not_mutate() -> None:
    """PROPOSE tools route through call_tool and publish — never execute."""
    with patch("tools.propose_tools.get_publisher") as gp:
        publisher = gp.return_value
        result = call_tool(
            "propose_risk_limit_change",
            {"rule": "max_daily_loss_pct", "new_value": 2.0, "rationale": "test"},
        )
        assert result["status"] == "queued_for_human_approval"
        # Two publishes: AI_PROPOSALS + AUDIT_LOG
        assert publisher.publish.call_count == 2
