"""AI agent permission tiers.

Three levels, enforced before any tool executes:

  READ      — AI executes autonomously (balances, PnL, spreads, reports)
  PROPOSE   — AI emits a proposal event; human approves via Slack
  NEVER     — Hardcoded blocked. Withdrawals and leverage are NEVER callable
              by an AI agent. The gate fails closed in production AND in tests.

`TOOL_PERMISSIONS` is the single source of truth. Adding a tool to the MCP
surface requires a matching entry here, or it cannot be invoked.
"""

from __future__ import annotations

from enum import Enum


class PermissionLevel(str, Enum):
    READ = "read"
    PROPOSE = "propose"
    NEVER = "never"


class ToolBlockedError(PermissionError):
    """Raised when a NEVER-tier tool is invoked. Crash loud — never swallow."""


TOOL_PERMISSIONS: dict[str, PermissionLevel] = {
    # READ — AI executes autonomously
    "get_balances": PermissionLevel.READ,
    "get_daily_pnl": PermissionLevel.READ,
    "get_open_positions": PermissionLevel.READ,
    "get_exchange_health": PermissionLevel.READ,
    "get_opportunity_feed": PermissionLevel.READ,
    "generate_daily_report": PermissionLevel.READ,
    "detect_anomalies": PermissionLevel.READ,
    # PROPOSE — AI proposes; human approves via Slack
    "propose_risk_limit_change": PermissionLevel.PROPOSE,
    "propose_size_adjustment": PermissionLevel.PROPOSE,
    "propose_strategy_pause": PermissionLevel.PROPOSE,
    # NEVER — hardcoded blocked
    "withdraw_funds": PermissionLevel.NEVER,
    "increase_leverage": PermissionLevel.NEVER,
    "add_exchange_key": PermissionLevel.NEVER,
    "disable_kill_switch": PermissionLevel.NEVER,
}


def require(tool_name: str) -> PermissionLevel:
    """Return the tool's permission tier; raise if NEVER or unregistered.

    An unregistered tool fails closed — you cannot ship a new tool without
    deciding its tier here.
    """
    level = TOOL_PERMISSIONS.get(tool_name)
    if level is None:
        raise ToolBlockedError(
            f"tool {tool_name!r} not registered in TOOL_PERMISSIONS — refusing to call"
        )
    if level is PermissionLevel.NEVER:
        raise ToolBlockedError(f"tool {tool_name!r} is permanently blocked for AI agents")
    return level
