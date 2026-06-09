"""Structured health checks + status aggregation.

Trust/reliability is the platform's #1 priority (see docs/MASTER_STRATEGY.md), so
health is a first-class, structured primitive — not an ad-hoc 200. Every service
exposes /healthz; the status-service aggregates dependency probes into one
platform status with per-component detail.

Pure + dependency-free: probes are injected callables, so this is fully testable
without Redis/HTTP. ``aggregate`` is criticality-aware — a non-critical component
degrades the platform; only a critical one marks it down.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class Component:
    name: str
    status: HealthStatus
    latency_ms: float | None = None
    detail: str = ""
    critical: bool = True


def run_check(name: str, probe: Callable[[], bool], critical: bool = True) -> Component:
    """Run one probe, timing it. ``probe()`` returns True for healthy; any
    exception (or False) marks the component DOWN. Never raises."""
    t0 = time.perf_counter()
    try:
        ok = bool(probe())
        latency = round((time.perf_counter() - t0) * 1000.0, 1)
        return Component(name, HealthStatus.OK if ok else HealthStatus.DOWN,
                         latency_ms=latency, critical=critical,
                         detail="" if ok else "probe returned false")
    except Exception as e:  # connection refused, timeout, etc.
        latency = round((time.perf_counter() - t0) * 1000.0, 1)
        return Component(name, HealthStatus.DOWN, latency_ms=latency,
                         detail=str(e)[:120], critical=critical)


def aggregate(components: list[Component]) -> HealthStatus:
    """Overall status: DOWN if any CRITICAL component is down; DEGRADED if any
    component is down/degraded (but no critical-down); else OK."""
    if any(c.status is HealthStatus.DOWN and c.critical for c in components):
        return HealthStatus.DOWN
    if any(c.status in (HealthStatus.DOWN, HealthStatus.DEGRADED) for c in components):
        return HealthStatus.DEGRADED
    return HealthStatus.OK


@dataclass
class StatusReport:
    status: HealthStatus
    components: list[Component] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "components": [
                {"name": c.name, "status": c.status.value, "latency_ms": c.latency_ms,
                 "detail": c.detail, "critical": c.critical}
                for c in self.components
            ],
        }


def build_report(components: list[Component]) -> StatusReport:
    return StatusReport(status=aggregate(components), components=components)
