"""corridor-engine — Africa cross-border corridor arbitrage (ALERT-ONLY).

Scores stablecoin-bridged FX corridors and flags viable ones. Deliberately
alert-only: a viable score is a signal for a human to settle, NOT an instruction
to move money — real settlement is money-transmission/VASP-regulated and gated on
licensing (docs/REGULATORY_BRIEF.md).

Endpoints:
  GET  /healthz
  POST /score   — score one corridor quote
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field

from scorer import CorridorQuote, score_corridor
from shared.pubsub.publisher import Topic, get_publisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("corridor-engine")

app = FastAPI(title="corridor-engine", version="0.1.0")

_publisher = None


def _get_publisher():
    global _publisher
    if _publisher is None:
        _publisher = get_publisher()
    return _publisher


class QuoteIn(BaseModel):
    corridor: str = Field(description="e.g. 'NGN->ZAR'")
    base_asset: str = "USDT"
    amount_source: float = Field(gt=0.0)
    stablecoin_per_source: float = Field(gt=0.0)
    dest_per_stablecoin: float = Field(gt=0.0)
    official_fx_dest_per_source: float = Field(gt=0.0)
    buy_fee_bps: float = 0.0
    sell_fee_bps: float = 0.0
    conversion_cost_bps: float = 0.0
    payout_friction_bps: float = 0.0
    settlement_time_hours: float = 0.0
    fx_volatility_bps_per_hour: float = 0.0
    venue_reliability: float = Field(default=1.0, ge=0.0, le=1.0)


class ScoreOut(BaseModel):
    corridor: str
    base_asset: str
    gross_edge_bps: float
    settlement_risk_bps: float
    total_cost_bps: float
    net_edge_bps: float
    settlement_confidence: float
    viable: bool
    breakdown: dict


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _score_one(q: QuoteIn) -> ScoreOut:
    result = score_corridor(CorridorQuote(**q.model_dump()))
    out = ScoreOut(**result.__dict__)
    if result.viable:
        # Alert-only: publish for a human to settle (notification-dispatcher) —
        # never auto-execute. NullPublisher logs locally (GCP_PROJECT_ID unset).
        _get_publisher().publish(Topic.CORRIDOR_ALERTS, out, attributes={"corridor": result.corridor})
        logger.info("VIABLE corridor=%s net_edge=%.0fbps confidence=%.2f",
                    result.corridor, result.net_edge_bps, result.settlement_confidence)
    return out


@app.post("/score", response_model=ScoreOut)
def score(q: QuoteIn) -> ScoreOut:
    return _score_one(q)


class ScanIn(BaseModel):
    quotes: list[QuoteIn]


class ScanOut(BaseModel):
    scanned: int
    viable: list[ScoreOut]  # best net edge first


@app.post("/scan", response_model=ScanOut)
def scan(body: ScanIn) -> ScanOut:
    """Score many corridors at once; return the viable ones ranked by net edge.
    The realistic path — a sweep over live corridor quotes."""
    scored = [_score_one(q) for q in body.quotes]
    viable = sorted((s for s in scored if s.viable), key=lambda s: s.net_edge_bps, reverse=True)
    return ScanOut(scanned=len(scored), viable=viable)
