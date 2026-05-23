"""MCP server exposing the AI ops tools to Claude/Gemini.

All tools route through `permissions.require()` first. NEVER-tier tools are
NOT registered with the MCP surface — they cannot be discovered, let alone
invoked. PROPOSE-tier tools emit Pub/Sub events instead of mutating state.

Run modes:
  - `mcp_server.run_stdio()` — stdio transport (default for local Claude Code)
  - the FastAPI app in main.py mounts the same tools over HTTP for Cloud Run

CRITICAL INVARIANT (enforced here):
  list_tools() MUST NOT return any tool whose permission tier is
  PermissionLevel.NEVER. The dispatcher additionally refuses NEVER tools at
  call time via permissions.require(). Defence in depth.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from permissions import TOOL_PERMISSIONS, PermissionLevel, require
from tools import (
    detect_anomalies,
    generate_daily_report,
    get_balances,
    get_daily_pnl,
    get_exchange_health,
    get_open_positions,
    get_opportunity_feed,
    propose_risk_limit_change,
    propose_size_adjustment,
    propose_strategy_pause,
)

logger = logging.getLogger(__name__)


# Registry maps tool name → callable. The MCP server discovers tools from here;
# permissions.require() gates each call.
#
# NOTE: NEVER-tier tools (withdraw_funds, increase_leverage, add_exchange_key,
# disable_kill_switch) intentionally have NO entry here. They cannot be
# dispatched because there is nothing to dispatch to.
_TOOL_IMPLS = {
    "get_balances": get_balances,
    "get_daily_pnl": get_daily_pnl,
    "get_open_positions": get_open_positions,
    "get_exchange_health": get_exchange_health,
    "get_opportunity_feed": get_opportunity_feed,
    "generate_daily_report": generate_daily_report,
    "detect_anomalies": detect_anomalies,
    "propose_risk_limit_change": propose_risk_limit_change,
    "propose_size_adjustment": propose_size_adjustment,
    "propose_strategy_pause": propose_strategy_pause,
}


_TOOL_DESCRIPTIONS = {
    "get_balances": "Return current capital and per-venue exposure (USD).",
    "get_daily_pnl": "PnL summary for a date (YYYY-MM-DD). Defaults to today.",
    "get_open_positions": "All currently-open trades.",
    "get_exchange_health": "Per-exchange API latency and kill-switch status.",
    "get_opportunity_feed": "Last 6h of opportunities (executed + rejected).",
    "generate_daily_report": "Structured daily report with embedded LLM prompt.",
    "detect_anomalies": "Flag slippage and rejection anomalies vs 30-day baseline.",
    "propose_risk_limit_change": "PROPOSE a risk-limit change. Requires human Slack approval.",
    "propose_size_adjustment": "PROPOSE a new per-trade size. Requires human Slack approval.",
    "propose_strategy_pause": "PROPOSE pausing a strategy. Requires human Slack approval.",
}


def list_tools() -> list[dict[str, str]]:
    """Return the advertised tool catalog.

    INVARIANT: NEVER-tier tools are filtered out — they are not even
    discoverable through this surface. Clients see only READ and PROPOSE.
    """
    advertised = []
    for name, level in TOOL_PERMISSIONS.items():
        if level is PermissionLevel.NEVER:
            continue
        if name not in _TOOL_IMPLS:
            # Defensive: never advertise a tool we cannot dispatch.
            continue
        advertised.append(
            {
                "name": name,
                "permission": level.value,
                "description": _TOOL_DESCRIPTIONS.get(name, ""),
            }
        )
    return advertised


def call_tool(name: str, args: dict[str, Any] | None = None) -> Any:
    """Single dispatch point. Every call passes through permission check.

    Raises:
        ToolBlockedError: if `name` is NEVER-tier or unregistered.
    """
    level = require(name)  # raises ToolBlockedError on NEVER or unregistered
    impl = _TOOL_IMPLS.get(name)
    if impl is None:
        # Should be unreachable — require() would have raised. Belt-and-braces.
        from permissions import ToolBlockedError

        raise ToolBlockedError(f"tool {name!r} has no implementation registered")
    args = args or {}
    logger.info(
        "ai-ops call name=%s level=%s args_keys=%s",
        name,
        level.value,
        list(args.keys()),
    )
    return impl(**args)


def build_mcp_server():
    """Construct the MCP server with all READ + PROPOSE tools registered.

    NEVER tools are not registered with the server. They cannot be listed
    via the MCP `tools/list` JSON-RPC method or invoked via `tools/call`.
    """
    from mcp.server import FastMCP

    server = FastMCP(name="ai-ops-agent")

    @server.tool()
    def get_balances_tool() -> dict[str, Any]:
        """Return current capital and per-venue exposure."""
        return call_tool("get_balances")

    @server.tool()
    def get_daily_pnl_tool(target_date: str | None = None) -> dict[str, Any]:
        """PnL summary for a date (YYYY-MM-DD). Defaults to today."""
        d = date.fromisoformat(target_date) if target_date else None
        return call_tool("get_daily_pnl", {"target_date": d})

    @server.tool()
    def get_open_positions_tool() -> dict[str, Any]:
        """All currently-open trades."""
        return call_tool("get_open_positions")

    @server.tool()
    def get_exchange_health_tool() -> dict[str, Any]:
        """Per-exchange API latency and kill-switch status."""
        return call_tool("get_exchange_health")

    @server.tool()
    def get_opportunity_feed_tool(limit: int = 25) -> dict[str, Any]:
        """Last 6h of opportunities (executed + rejected)."""
        return call_tool("get_opportunity_feed", {"limit": limit})

    @server.tool()
    def generate_daily_report_tool(target_date: str | None = None) -> dict[str, Any]:
        """Structured daily report with embedded LLM prompt."""
        d = date.fromisoformat(target_date) if target_date else None
        return call_tool("generate_daily_report", {"target_date": d})

    @server.tool()
    def detect_anomalies_tool(target_date: str | None = None) -> dict[str, Any]:
        """Flag slippage and rejection anomalies vs 30-day baseline."""
        d = date.fromisoformat(target_date) if target_date else None
        return call_tool("detect_anomalies", {"target_date": d})

    @server.tool()
    def propose_risk_limit_change_tool(
        rule: str, new_value: float, rationale: str
    ) -> dict[str, str]:
        """PROPOSE a risk-limit change. Requires human Slack approval."""
        return call_tool(
            "propose_risk_limit_change",
            {"rule": rule, "new_value": new_value, "rationale": rationale},
        )

    @server.tool()
    def propose_size_adjustment_tool(new_size_usd: float, rationale: str) -> dict[str, str]:
        """PROPOSE a new per-trade size. Requires human Slack approval."""
        return call_tool(
            "propose_size_adjustment",
            {"new_size_usd": new_size_usd, "rationale": rationale},
        )

    @server.tool()
    def propose_strategy_pause_tool(
        strategy: str, rationale: str, duration_minutes: int = 60
    ) -> dict[str, str]:
        """PROPOSE pausing a strategy. Requires human Slack approval."""
        return call_tool(
            "propose_strategy_pause",
            {
                "strategy": strategy,
                "rationale": rationale,
                "duration_minutes": duration_minutes,
            },
        )

    return server


def run_stdio() -> None:
    server = build_mcp_server()
    server.run()


if __name__ == "__main__":
    run_stdio()
