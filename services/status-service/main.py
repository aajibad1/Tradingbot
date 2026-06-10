"""status-service — aggregates platform health into one status (reliability #1).

Probes Redis + each configured service's /healthz and returns a structured,
criticality-aware platform status. Read-only. Powers the status page + uptime SLOs.

Config (env):
  REDIS_URL                 — probed if set (critical)
  STATUS_TARGETS            — comma list of name=url (each url's /healthz probed)
  STATUS_NONCRITICAL        — comma list of target names that only DEGRADE (not DOWN)

Endpoints:
  GET  /healthz      — this service's own liveness
  GET  /status       — aggregated platform status
  GET  /slo/catalog  — documented SLO targets (machine-readable)
  POST /slo/evaluate — error-budget + burn-rate verdict for observed counts
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shared.health import Component, HealthStatus, build_report, run_check
from shared.http import install_contract
from shared.slo import DEFAULT_SLOS, SLOSeverity, evaluate_all, worst_severity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("status-service")

app = FastAPI(title="status-service", version="0.1.0")
# Platform API contract (docs/06): x-request-id / x-correlation-id propagation +
# standard error envelope. status-service serves the status page, so correlatable
# requests + a uniform error shape matter for the ops/uptime tooling that polls it.
install_contract(app, service_name="status-service")


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
    # Status semantics (docs/SLOS.md): ok and degraded both still SERVE, so they
    # return 200 — only a critical-down (DOWN) returns 503 to trip external uptime
    # monitors. Returning 503 for degraded would page on-call for a platform that
    # is, by definition, still serving.
    code = 503 if report.status is HealthStatus.DOWN else 200
    return JSONResponse(report.to_dict(), status_code=code)


@app.get("/slo/catalog")
def slo_catalog() -> dict:
    """The documented SLO targets, in machine-readable form (single source of truth
    in shared/slo.py:DEFAULT_SLOS, mirroring docs/SLOS.md)."""
    return {
        "slos": [
            {"name": t.name, "objective": t.objective,
             "window_days": t.window_days, "description": t.description}
            for t in DEFAULT_SLOS.values()
        ]
    }


class SLOEvaluateRequest(BaseModel):
    # name → [good, total] observed over the SLO window. A metrics job (BigQuery
    # event lag, probe history) supplies these; the math is in shared/slo.py.
    observations: dict[str, list[int]] = Field(default_factory=dict)


@app.post("/slo/evaluate")
def slo_evaluate(req: SLOEvaluateRequest) -> JSONResponse:
    """Evaluate observed good/total counts against the SLO catalog.

    Returns each SLO's error-budget position + burn-rate severity, rolled up to a
    worst-severity. A PAGE-level burn returns 503 (same semantics as /status — it
    trips external monitors); ticket/ok return 200.
    """
    obs = {k: (v[0], v[1]) for k, v in req.observations.items() if len(v) == 2}
    statuses = evaluate_all(obs)
    worst = worst_severity(statuses)
    code = 503 if worst is SLOSeverity.PAGE else 200
    return JSONResponse(
        {"worst_severity": worst.value, "slos": statuses}, status_code=code
    )
