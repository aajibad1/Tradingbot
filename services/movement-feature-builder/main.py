"""movement-feature-builder — market window → movement features (hybrid system).

Computes the features the signal-engine + regime-classifier consume from a window
of normalized observations. Deterministic; no execution.

Endpoints:
  GET  /healthz
  POST /features   — {symbol, now_ms, window: [observations]}
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field

from features import Observation, build_features, features_dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("movement-feature-builder")

app = FastAPI(title="movement-feature-builder", version="0.1.0")


class ObsIn(BaseModel):
    ts_ms: int
    venue: str
    bid: float = Field(gt=0.0)
    ask: float = Field(gt=0.0)
    bid_size: float = 0.0
    ask_size: float = 0.0


class FeaturesRequest(BaseModel):
    symbol: str
    now_ms: int
    window: list[ObsIn]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/features")
def features(req: FeaturesRequest) -> dict:
    window = [Observation(**o.model_dump()) for o in req.window]
    return features_dict(build_features(req.symbol, window, req.now_ms))
