"""Trading-eligibility snapshot — the control-plane → execution-plane seam.

When a tenant's onboarding / plan / live state changes, core-api writes a compact
snapshot to Redis (``eligibility:t:{tenant}``). risk-engine reads it as an
entitlement gate, so an opportunity can't be approved for a tenant that hasn't
completed onboarding (and, later, can't trade a market its plan doesn't include).

Local/dev/test (REDIS_URL unset) is a no-op — like the rest of the repo's
"works without cloud deps" convention. ``build_snapshot`` is pure and testable.
"""

from __future__ import annotations

import json
import logging
import os

import billing
from db import OnboardingStatus, Tenant
from shared.tenant import eligibility_key

logger = logging.getLogger("core-api.eligibility")


def build_snapshot(tenant: Tenant) -> dict:
    ent = billing.entitlements_for(tenant.plan)
    return {
        "trading_ready": tenant.onboarding_status is OnboardingStatus.TRADING_READY,
        "live_enabled": tenant.live_enabled,
        "live_allowed": ent.live_trading,
        "markets": ent.markets,
        "max_live_capital_usd": ent.max_live_capital_usd,
        "plan": tenant.plan.value,
    }


def publish_snapshot(tenant: Tenant) -> None:
    """Write the tenant's eligibility snapshot to Redis. No-op when REDIS_URL is
    unset (local/test) — the gate then simply has nothing to enforce against."""
    url = os.environ.get("REDIS_URL")
    if not url:
        return
    try:
        import redis  # lazy: only needed when a Redis is configured

        client = redis.from_url(url)
        client.set(eligibility_key(tenant.id), json.dumps(build_snapshot(tenant)))
    except Exception as e:  # never fail the request because the snapshot didn't publish
        logger.warning("eligibility snapshot publish failed for tenant=%s: %s", tenant.id, e)
