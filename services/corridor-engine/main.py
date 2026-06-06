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
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel, Field

from scorer import CorridorQuote, score_corridor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("corridor-engine")

app = FastAPI(title="corridor-engine", version="0.1.0")


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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/score")
def score(q: QuoteIn) -> dict:
    result = score_corridor(CorridorQuote(**q.model_dump()))
    if result.viable:
        # Alert-only seam: a viable corridor would notify (notification-dispatcher)
        # for a human to settle — never auto-execute.
        logger.info("VIABLE corridor=%s net_edge=%.0fbps confidence=%.2f",
                    result.corridor, result.net_edge_bps, result.settlement_confidence)
    return asdict(result)
