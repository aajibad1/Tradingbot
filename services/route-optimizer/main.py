"""route-optimizer — advisory route ranking (hybrid system). Never executes."""
from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel, Field

from router import RouteCandidate, rank_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("route-optimizer")
app = FastAPI(title="route-optimizer", version="0.1.0")


class CandidateIn(BaseModel):
    venue: str
    size_usd: float = Field(gt=0.0)
    expected_depth_usd: float = 0.0
    taker_fee_bps: float = 0.0
    est_slippage_bps: float = 0.0
    latency_ms: float = 0.0
    prior_fill_rate: float = Field(default=1.0, ge=0.0, le=1.0)


class RankRequest(BaseModel):
    candidates: list[CandidateIn]
    # Optional realized-slippage calibration: the report from the trade-ledger
    # /ml-export/route-quality endpoint. When present, venue slippage priors are
    # corrected from observed fills before ranking (cold-start venues untouched).
    route_quality: dict | None = None


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/rank")
def rank(req: RankRequest) -> dict:
    ranking = rank_routes(
        [RouteCandidate(**c.model_dump()) for c in req.candidates],
        route_quality=req.route_quality,
    )
    return asdict(ranking)
