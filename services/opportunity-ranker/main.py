"""opportunity-ranker — advisory scoring (AI plane). NEVER approves trades.

A viable score advises ranking/route preference; the risk engine still applies
all hard rules afterwards. See ranker.py.

Endpoints:
  GET  /healthz
  POST /score   — score one opportunity's features (advisory)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ranker import Features, rank

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("opportunity-ranker")

app = FastAPI(title="opportunity-ranker", version="0.1.0")


class FeaturesIn(BaseModel):
    net_edge_bps: float
    size_usd: float = Field(default=10_000.0, gt=0.0)
    top_of_book_depth_usd: float = 0.0
    book_imbalance: float = Field(default=0.0, ge=0.0, le=1.0)
    recent_volatility_bps: float = 0.0
    venue_health_score: float = Field(default=1.0, ge=0.0, le=1.0)
    historical_slippage_bps: float = 0.0


class RankingOut(BaseModel):
    profitability_probability: float
    expected_net_bps: float
    confidence_score: float
    recommended_action: str
    top_features: list[str]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/score", response_model=RankingOut)
def score(f: FeaturesIn) -> RankingOut:
    r = rank(Features(**f.model_dump()))
    return RankingOut(**r.__dict__)
