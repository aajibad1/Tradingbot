"""venue-anomaly-detector — flag degraded venue feeds (hybrid system, advisory).

Config (env):
  CONNECTOR_RUNTIME_URL — opt-in: push each verdict to connector-runtime's health
  read model (docs/03 — "one catalog instead of knowing vendors"), so a degraded
  feed shows up where the platform already looks. Fail-soft; advisory only.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict

from urllib.parse import quote

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

from anomaly import Severity, VenueSignals, detect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("venue-anomaly-detector")
app = FastAPI(title="venue-anomaly-detector", version="0.1.0")


class SignalsIn(BaseModel):
    # Venue names are identifiers (kraken, crypto.com, binance.us) — the pattern
    # rejects URL metacharacters and dot-segments so a crafted venue can never
    # rewrite the connector-runtime request path (e.g. into /disable).
    venue: str = Field(pattern=r"^[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)*$")
    quote_staleness_ms: float = Field(default=0.0, ge=0.0)
    spread_bps: float = Field(default=0.0, ge=0.0)
    expected_spread_bps: float = Field(default=0.0, ge=0.0)
    sequence_gap_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    update_rate_ratio: float = Field(default=1.0, ge=0.0)
    rejection_rate: float = Field(default=0.0, ge=0.0, le=1.0)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# Venues WE last marked unhealthy — so a clean verdict only ever CLEARS our own
# report. Blindly posting healthy=true on every clean detect would clobber state
# owned by other reporters (e.g. a latency-DEGRADED set by a venue adapter).
_reported_down: set[str] = set()


def _push_to_connector_runtime(report) -> None:
    """Report the verdict to connector-runtime's health read model (opt-in via
    CONNECTOR_RUNTIME_URL). Semantics:

    - CRITICAL → healthy=false (catalog DOWN). WARN is advisory-only and never
      pushed — mapping a lone spread jump to DOWN would be severity inflation.
    - a clean/WARN verdict posts healthy=true ONLY when this detector previously
      marked the venue down (clearing our own report, nobody else's).

    Fail-soft: a missing/unregistered venue (404) or an unreachable runtime never
    fails /detect — this signal is advisory, and the detector stays a pure
    calculator when the env is unset."""
    base = os.environ.get("CONNECTOR_RUNTIME_URL")
    if not base:
        return
    critical = report.status is Severity.CRITICAL
    if not critical and report.venue not in _reported_down:
        return  # nothing to report: not down, and no prior report of ours to clear
    kinds = ",".join(x.kind for x in report.anomalies)
    body = ({"healthy": False, "detail": f"critical anomalies: {kinds}"} if critical
            else {"healthy": True, "detail": "ok" if not kinds else f"warn anomalies: {kinds}"})
    try:
        r = httpx.post(f"{base.rstrip('/')}/v1/connectors/{quote(report.venue, safe='')}/health",
                       json=body, timeout=3.0)
        if r.status_code == 404:
            logger.info("venue=%s not registered in connector-runtime; skipped", report.venue)
            return
    except Exception:  # noqa: BLE001 — advisory push must never break /detect
        logger.warning("connector-runtime unreachable; verdict for %s not pushed", report.venue)
        return
    (_reported_down.add if critical else _reported_down.discard)(report.venue)


@app.post("/detect")
def detect_anomalies(s: SignalsIn) -> dict:
    report = detect(VenueSignals(**s.model_dump()))
    if report.degraded:
        logger.info("venue=%s status=%s anomalies=%s", report.venue, report.status.value,
                    [x.kind for x in report.anomalies])
    _push_to_connector_runtime(report)
    return asdict(report)
