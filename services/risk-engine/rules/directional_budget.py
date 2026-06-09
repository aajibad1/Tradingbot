"""Hybrid directional-sleeve budget.

The core book is market-neutral arbitrage; a *satellite* sleeve may take net
directional (non-neutral) exposure for upside. This rule enforces the split:
neutral opportunities are ALWAYS allowed here; directional ones are admitted only
while the tenant's net directional exposure stays within
``max_directional_exposure_pct`` (default 0.0 ⇒ pure neutral, directional refused).

This is the deterministic risk layer for the hybrid model — AI may rank/suppress
directional signals upstream, but this hard cap (and the kill switch / drawdown
guard) own the downside.
"""

from __future__ import annotations

from shared.models.opportunity import Opportunity, StrategyType
from shared.models.risk_state import RiskRule, RiskState, RiskViolation


def is_directional(opportunity: Opportunity) -> bool:
    return opportunity.strategy == StrategyType.DIRECTIONAL


def check_directional_budget(
    opportunity: Opportunity, state: RiskState, limits: dict[str, float]
) -> list[RiskViolation]:
    if not is_directional(opportunity):
        return []  # neutral core — not gated by the directional budget
    incremental_pct = opportunity.recommended_size_usd / state.capital_usd * 100.0
    projected = state.directional_exposure_pct + incremental_pct
    limit = limits["max_directional_exposure_pct"]
    if projected > limit:
        return [
            RiskViolation(
                rule=RiskRule.DIRECTIONAL_BUDGET,
                observed=projected,
                limit=limit,
                message=(
                    f"projected directional exposure {projected:.2f}% exceeds "
                    f"budget {limit:.2f}%"
                ),
            )
        ]
    return []
