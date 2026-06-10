"""Usage metering aggregation (docs/06 metered billing). Pure + testable.

Counts API usage per tenant per billing period (monthly), broken down by route, so
the platform can bill on volume and flag overages. The billing unit is the tenant
(the partner-auth tenant_id); api_key_id is recorded for attribution but not the
billing key.

Overage policy here is SOFT: recording over quota still succeeds and flags
``over_quota`` — metered billing bills the overage rather than hard-blocking. A
caller that wants a hard gate can pre-check via :meth:`would_exceed`.
"""

from __future__ import annotations

from datetime import datetime


def current_period(now: datetime) -> str:
    """Monthly billing bucket, e.g. '2026-06'."""
    return f"{now.year:04d}-{now.month:02d}"


class Meter:
    def __init__(self) -> None:
        # usage[(tenant, period)] = {"total": int, "by_route": {route: int}}
        self._usage: dict[tuple[str, str], dict] = {}
        # quota[tenant] = monthly_limit (int). Absent = unlimited.
        self._quota: dict[str, int] = {}

    # -- quotas ---------------------------------------------------------- #
    def set_quota(self, tenant: str, monthly_limit: int) -> None:
        self._quota[tenant] = monthly_limit

    def get_quota(self, tenant: str) -> int | None:
        return self._quota.get(tenant)

    # -- recording ------------------------------------------------------- #
    def record(self, tenant: str, route: str, period: str, count: int = 1) -> dict:
        if count <= 0:
            raise ValueError("count must be positive")
        bucket = self._usage.setdefault((tenant, period), {"total": 0, "by_route": {}})
        bucket["total"] += count
        bucket["by_route"][route] = bucket["by_route"].get(route, 0) + count
        return self.usage(tenant, period)

    def would_exceed(self, tenant: str, period: str, count: int = 1) -> bool:
        limit = self._quota.get(tenant)
        if limit is None:
            return False
        current = self._usage.get((tenant, period), {}).get("total", 0)
        return current + count > limit

    # -- reporting ------------------------------------------------------- #
    def usage(self, tenant: str, period: str) -> dict:
        bucket = self._usage.get((tenant, period), {"total": 0, "by_route": {}})
        limit = self._quota.get(tenant)
        total = bucket["total"]
        return {
            "tenant_id": tenant,
            "period": period,
            "total": total,
            "by_route": dict(bucket["by_route"]),
            "quota": limit,
            "over_quota": limit is not None and total > limit,
            "remaining": (max(0, limit - total) if limit is not None else None),
        }
