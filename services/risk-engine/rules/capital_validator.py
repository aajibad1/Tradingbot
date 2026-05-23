"""Validate that deployed capital is large enough to cover GCP infra costs.

The arbitrage system has a fixed monthly infrastructure cost (Cloud Run +
Redis + BigQuery + Pub/Sub egress). Below a certain capital level, the
expected gross return cannot cover that cost — net monthly P&L is
negative-by-construction.

This rule does NOT block opportunities directly. It runs in three modes:

  * As an HTTP endpoint (``GET /capital-validation``) — operators read.
  * As an automatic gate before the kill switch is reset — refuse to reset
    while the system is configured below break-even.
  * As an informational field on ``/state`` — surfaces to the dashboard.

Failing capital validation does NOT trip the kill switch. The intent is
prevention (don't reset into a money-losing posture), not reaction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# Conservative defaults — calibrated from the GCP cost estimator in
# infra/terraform/environments/prod (Cloud Run min-instances=1 across
# 8 services + redis + bigquery storage).
DEFAULT_MONTHLY_INFRA_COST_USD = 300.0
DEFAULT_EXPECTED_ANNUAL_RETURN_PCT = 0.15  # 15% — conservative carry assumption
DEFAULT_MIN_VIABLE_CAPITAL_USD = 25_000.0   # below this, net monthly < 0
DEFAULT_RECOMMENDED_CAPITAL_USD = 50_000.0  # first comfortably profitable level


@dataclass(frozen=True)
class CapitalValidationResult:
    passed: bool
    capital_usd: float
    monthly_infra_cost_usd: float
    expected_monthly_gross_usd: float
    expected_monthly_net_usd: float
    min_viable_usd: float
    recommended_usd: float
    message: str


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def validate_capital(capital_usd: float) -> CapitalValidationResult:
    """Return the capital-validation snapshot. Pure function."""
    infra = _env_float("MONTHLY_INFRA_COST_USD", DEFAULT_MONTHLY_INFRA_COST_USD)
    annual = _env_float("EXPECTED_ANNUAL_RETURN_PCT", DEFAULT_EXPECTED_ANNUAL_RETURN_PCT)
    min_viable = _env_float("MIN_VIABLE_CAPITAL_USD", DEFAULT_MIN_VIABLE_CAPITAL_USD)
    recommended = _env_float("RECOMMENDED_CAPITAL_USD", DEFAULT_RECOMMENDED_CAPITAL_USD)

    gross_monthly = capital_usd * annual / 12.0
    net_monthly = gross_monthly - infra
    passed = capital_usd >= min_viable

    if not passed:
        message = (
            f"capital ${capital_usd:,.0f} below minimum viable ${min_viable:,.0f}; "
            f"est. net monthly ${net_monthly:,.0f} (gross ${gross_monthly:,.0f} - "
            f"infra ${infra:,.0f}); recommend ${recommended:,.0f}+"
        )
    elif capital_usd < recommended:
        message = (
            f"capital ${capital_usd:,.0f} meets minimum but below recommended "
            f"${recommended:,.0f}; est. net monthly ${net_monthly:,.0f}"
        )
    else:
        message = (
            f"capital ${capital_usd:,.0f} validated; est. net monthly "
            f"${net_monthly:,.0f}"
        )

    return CapitalValidationResult(
        passed=passed,
        capital_usd=capital_usd,
        monthly_infra_cost_usd=infra,
        expected_monthly_gross_usd=gross_monthly,
        expected_monthly_net_usd=net_monthly,
        min_viable_usd=min_viable,
        recommended_usd=recommended,
        message=message,
    )
