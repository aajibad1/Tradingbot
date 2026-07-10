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

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

from anomaly import VenueSignals, detect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("venue-anomaly-detector")
app = FastAPI(title="venue-anomaly-detector", version="0.1.0")


class SignalsIn(BaseModel):
    venue: str
    quote_staleness_ms: float = Field(default=0.0, ge=0.0)
    spread_bps: float = Field(default=0.0, ge=0.0)
    expected_spread_bps: float = Field(default=0.0, ge=0.0)
    sequence_gap_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    update_rate_ratio: float = Field(default=1.0, ge=0.0)
    rejection_rate: float = Field(default=0.0, ge=0.0, le=1.0)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _push_to_connector_runtime(report) -> None:
    """Report the verdict to connector-runtime's health read model (opt-in via
    CONNECTOR_RUNTIME_URL). Fail-soft: a missing/unregistered venue (404) or an
    unreachable runtime never fails /detect — this signal is advisory, and the
    detector stays a pure calculator when the env is unset."""
    base = os.environ.get("CONNECTOR_RUNTIME_URL")
    if not base:
        return
    detail = ("ok" if not report.degraded
              else "anomalies: " + ",".join(x.kind for x in report.anomalies))
    try:
        r = httpx.post(f"{base.rstrip('/')}/v1/connectors/{report.venue}/health",
                       json={"healthy": not report.degraded, "detail": detail}, timeout=3.0)
        if r.status_code == 404:
            logger.info("venue=%s not registered in connector-runtime; skipped", report.venue)
    except Exception:  # noqa: BLE001 — advisory push must never break /detect
        logger.warning("connector-runtime unreachable; verdict for %s not pushed", report.venue)


@app.post("/detect")
def detect_anomalies(s: SignalsIn) -> dict:
    report = detect(VenueSignals(**s.model_dump()))
    if report.degraded:
        logger.info("venue=%s status=%s anomalies=%s", report.venue, report.status.value,
                    [x.kind for x in report.anomalies])
    _push_to_connector_runtime(report)
    return asdict(report)
