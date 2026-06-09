"""regime-classifier — deterministic market-regime labels (hybrid system).

Classifies the market environment so the signal-engine gates families and the
risk layer tightens. Advisory/deterministic — never executes. Rule-based first.

Endpoints:
  GET  /healthz
  POST /classify
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel, Field

from regime import RegimeFeatures, classify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("regime-classifier")

app = FastAPI(title="regime-classifier", version="0.1.0")


class FeaturesIn(BaseModel):
    realized_vol_bps: float = Field(default=0.0, ge=0.0)
    trend_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    book_depth_usd: float = Field(default=1_000_000.0, ge=0.0)
    spread_bps: float = Field(default=0.0, ge=0.0)
    mean_reversion_z: float = 0.0


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/classify")
def classify_regime(f: FeaturesIn) -> dict:
    result = classify(RegimeFeatures(**f.model_dump()))
    logger.info("regime=%s confidence=%.2f flags=%s", result.regime, result.confidence, result.flags)
    return asdict(result)
