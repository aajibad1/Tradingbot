"""Tenant eligibility gate — the execution-plane half of the control-plane seam.

Reads the eligibility snapshot core-api writes to ``eligibility:t:{tenant}`` and
refuses opportunities for a tenant that hasn't completed onboarding. The snapshot
is JSON: ``{trading_ready, live_enabled, live_allowed, markets, ...}``.

Backward-compatible by design:
  * the ``default`` tenant (legacy single-tenant deployment) is never gated;
  * a tenant with NO snapshot is allowed unless ``require=True`` — so existing
    multi-tenant flows keep working until eligibility is explicitly enforced via
    REQUIRE_TENANT_ELIGIBILITY=true. A snapshot that says not-ready always blocks.
"""

from __future__ import annotations

import json

from shared.models.risk_state import RiskRule, RiskViolation
from shared.tenant import DEFAULT_TENANT, eligibility_key


def _violation(message: str) -> RiskViolation:
    # observed/limit are numeric in the schema; 0/1 encodes ready=false/threshold.
    return RiskViolation(rule=RiskRule.TENANT_NOT_ELIGIBLE, observed=0.0, limit=1.0, message=message)


def check_eligibility(redis, tenant_id: str, *, require: bool = False) -> list[RiskViolation]:
    if tenant_id == DEFAULT_TENANT:
        return []
    raw = redis.get(eligibility_key(tenant_id))
    if raw is None:
        if require:
            return [_violation(f"tenant {tenant_id} has no eligibility snapshot (not onboarded)")]
        return []
    try:
        snap = json.loads(raw)
    except (ValueError, TypeError):
        return [_violation(f"tenant {tenant_id} eligibility snapshot is unreadable")]
    if not snap.get("trading_ready"):
        return [_violation(f"tenant {tenant_id} onboarding not trading_ready (plan={snap.get('plan')})")]
    return []
