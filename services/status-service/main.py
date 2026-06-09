"""status-service — aggregates platform health into one status (reliability #1).

Probes Redis + each configured service's /healthz and returns a structured,
criticality-aware platform status. Read-only. Powers the status page + uptime SLOs.

Config (env):
  REDIS_URL                 — probed if set (critical)
  STATUS_TARGETS            — comma list of name=url (each url's /healthz probed)
  STATUS_NONCRITICAL        — comma list of target names that only DEGRADE (not DOWN)

Endpoints:
  GET /healthz   — this service's own liveness
  GET /status    — aggregated platform status
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from shared.health import Component, HealthStatus, build_report, run_check

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("status-service")

app = FastAPI(title="status-service", version="0.1.0")


def _redis_probe() -> bool:
    import redis  # lazy
    return bool(redis.from_url(os.environ["REDIS_URL"], socket_timeout=2.0).ping())


def _http_probe(url: str):
    def _p() -> bool:
        import httpx
        return httpx.get(url, timeout=3.0).status_code == 200
    return _p


def _targets() -> list[tuple[str, str, bool]]:
    """(name, healthz_url, critical) from STATUS_TARGETS / STATUS_NONCRITICAL."""
    noncrit = {n.strip() for n in os.environ.get("STATUS_NONCRITICAL", "").split(",") if n.strip()}
    out = []
    for pair in os.environ.get("STATUS_TARGETS", "").split(","):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, url = pair.split("=", 1)
        name = name.strip()
        out.append((name, url.strip().rstrip("/") + "/healthz", name not in noncrit))
    return out


def collect(probes=None) -> list[Component]:
    """Run all configured probes. ``probes`` (for tests) overrides the live set:
    a list of (name, callable, critical)."""
    if probes is not None:
        return [run_check(n, p, critical=c) for n, p, c in probes]
    components: list[Component] = []
    if os.environ.get("REDIS_URL"):
        components.append(run_check("redis", _redis_probe, critical=True))
    for name, url, critical in _targets():
        components.append(run_check(name, _http_probe(url), critical=critical))
    return components


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
def status() -> JSONResponse:
    report = build_report(collect())
    # Surface unhealthy platform as a 503 so external uptime monitors trip.
    code = 200 if report.status is HealthStatus.OK else 503
    return JSONResponse(report.to_dict(), status_code=code)
