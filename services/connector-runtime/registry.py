"""Connector registry + health model (docs/03 shared-core, CONNECTOR_ARCHITECTURE.md).

The source of truth for *which* venues are pluggable (exchange, DEX, stablecoin
rail, FX feed) and whether each is healthy. Connectors register here and report
health; the platform reads the catalog + health summary instead of knowing about
specific vendors. Metadata + health signals only — no venue credentials, no I/O.

Reuses the region/market-type vocabulary from shared.connectors.adapter so the
registry can't drift from the adapter contract. Pure (raises ValueError / KeyError);
the FastAPI layer maps those to API errors.
"""

from __future__ import annotations

from shared.connectors.adapter import MARKET_TYPES, REGIONS

KINDS = ("cex", "dex", "rail", "fx", "reference")

# Status vocabulary.
ACTIVE = "active"
DEGRADED = "degraded"
DOWN = "down"
DISABLED = "disabled"

# A healthy connector slower than this is DEGRADED rather than ACTIVE.
LATENCY_DEGRADED_MS = 1000


class ConnectorRegistry:
    def __init__(self) -> None:
        self._c: dict[str, dict] = {}

    def register(self, venue: str, region: str, market_type: str, kind: str,
                 provider: str = "", status: str = ACTIVE) -> dict:
        if region not in REGIONS:
            raise ValueError(f"region must be one of {REGIONS}")
        if market_type not in MARKET_TYPES:
            raise ValueError(f"market_type must be one of {MARKET_TYPES}")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if status not in (ACTIVE, DEGRADED, DOWN, DISABLED):
            raise ValueError("invalid status")
        rec = {
            "venue": venue, "region": region, "market_type": market_type, "kind": kind,
            "provider": provider, "status": status,
            "healthy": None, "latency_ms": None, "detail": "", "last_health_at": None,
        }
        self._c[venue] = rec
        return rec

    def get(self, venue: str) -> dict:
        return self._c[venue]  # KeyError → 404 at the edge

    def report_health(self, venue: str, healthy: bool, latency_ms: int | None,
                      detail: str, now: str) -> dict:
        rec = self._c[venue]
        if rec["status"] == DISABLED:
            return rec  # a disabled connector ignores health pings
        rec["healthy"] = healthy
        rec["latency_ms"] = latency_ms
        rec["detail"] = detail
        rec["last_health_at"] = now
        if not healthy:
            rec["status"] = DOWN
        elif latency_ms is not None and latency_ms > LATENCY_DEGRADED_MS:
            rec["status"] = DEGRADED
        else:
            rec["status"] = ACTIVE
        return rec

    def disable(self, venue: str) -> dict:
        rec = self._c[venue]
        rec["status"] = DISABLED
        return rec

    def list(self, region: str | None = None, market_type: str | None = None,
             status: str | None = None) -> list[dict]:
        return [r for r in self._c.values()
                if (region is None or r["region"] == region)
                and (market_type is None or r["market_type"] == market_type)
                and (status is None or r["status"] == status)]

    def summary(self) -> dict:
        by_status: dict[str, int] = {}
        by_region: dict[str, int] = {}
        for r in self._c.values():
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
            by_region[r["region"]] = by_region.get(r["region"], 0) + 1
        unhealthy = [r["venue"] for r in self._c.values() if r["status"] in (DOWN, DEGRADED)]
        return {"total": len(self._c), "by_status": by_status,
                "by_region": by_region, "unhealthy": unhealthy}
