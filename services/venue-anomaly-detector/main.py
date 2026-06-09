"""venue-anomaly-detector — flag degraded venue feeds (hybrid system, advisory)."""
from __future__ import annotations

import logging
from dataclasses import asdict

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


@app.post("/detect")
def detect_anomalies(s: SignalsIn) -> dict:
    report = detect(VenueSignals(**s.model_dump()))
    if report.degraded:
        logger.info("venue=%s status=%s anomalies=%s", report.venue, report.status.value,
                    [x.kind for x in report.anomalies])
    return asdict(report)
