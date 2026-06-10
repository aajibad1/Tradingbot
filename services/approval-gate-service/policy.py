"""AI action permission policy (docs/10 agent governance; CLAUDE.md permission model).

The platform's non-negotiable AI permission model:
  - READ-only ops (analysis, ranking, reporting) are autonomous → auto-approved.
  - Risk-limit / trade-size / strategy changes are PROPOSE-only → require a human.
  - Withdrawals and leverage increases are HARD-BLOCKED → never executable by AI.

Anything unrecognized fails SAFE to PROPOSE (a human must look). Pure functions so
the classification is fully unit-testable and can't drift from the policy.
"""

from __future__ import annotations

# Classification outcomes.
AUTO = "auto_approved"       # read-only — proceeds without a human
PROPOSE = "pending_approval"  # sensitive — held for human approve/reject
BLOCKED = "blocked"          # hardcoded deny — AI may never do this

# Read-only / advisory actions — autonomous.
READ_ACTIONS = frozenset({
    "read", "query", "summarize", "report", "analyze", "rank", "score",
    "recommend", "classify", "explain", "research", "draft_message",
})

# Sensitive mutations — propose-only, require human approval.
PROPOSE_ACTIONS = frozenset({
    "risk_limit_change", "trade_size_change", "strategy_param_change",
    "execute_trade", "place_order", "cancel_order", "kill_switch_reset",
    "config_change", "pause_strategy",
})

# Hardcoded denials — never executable by AI, no human override via this gate.
BLOCKED_ACTIONS = frozenset({
    "withdrawal", "withdraw_funds", "transfer_funds", "leverage_increase",
    "increase_leverage", "move_funds", "payout",
})


def classify(action_type: str) -> str:
    """Map an action_type to AUTO / PROPOSE / BLOCKED. Unknown → PROPOSE (fail-safe)."""
    a = (action_type or "").strip().lower()
    if a in BLOCKED_ACTIONS:
        return BLOCKED
    if a in READ_ACTIONS:
        return AUTO
    if a in PROPOSE_ACTIONS:
        return PROPOSE
    return PROPOSE  # default fail-safe: anything unrecognized needs a human
