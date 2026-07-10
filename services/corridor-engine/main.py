"""corridor-engine — Africa cross-border corridor arbitrage (ALERT-ONLY).

Scores stablecoin-bridged FX corridors and flags viable ones. Deliberately
alert-only: a viable score is a signal for a human to settle, NOT an instruction
to move money — real settlement is money-transmission/VASP-regulated and gated on
licensing (docs/REGULATORY_BRIEF.md).

Endpoints:
  GET  /healthz
  POST /score   — score one corridor quote

Config (env):
  CORRIDOR_INTEL_URL — opt-in: when set and the caller omits venue_reliability,
  fetch it from corridor-intelligence-service /assess (fail-soft; an explicit
  caller value always wins).
"""

from __future__ import annotations

import logging
import os

import httpx
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


def _intel_reliability(corridor: str) -> tuple[float, str] | None:
    """Fetch (reliability_score, source) from corridor-intelligence-service — the
    enrichment its intelligence.py exists to provide. Opt-in (CORRIDOR_INTEL_URL)
    and fail-soft: any error returns None and scoring proceeds with the quote as
    given — intel is advisory and must never block or fail a score."""
    base = os.environ.get("CORRIDOR_INTEL_URL")
    if not base:
        return None
    try:
        r = httpx.post(f"{base.rstrip('/')}/assess", json={"corridor": corridor}, timeout=5.0)
        data = r.json()
        rel = max(0.0, min(1.0, float(data["reliability_score"])))
    except Exception:  # noqa: BLE001 — advisory enrichment must never break /score
        logger.warning("corridor intel unavailable for %s; scoring without it", corridor)
        return None
    return rel, str(data.get("source", "intel"))


def _score_one(q: QuoteIn) -> ScoreOut:
    quote = CorridorQuote(**q.model_dump())
    intel_note = None
    # Only fill reliability the caller did NOT supply — an explicit value (even an
    # explicit 1.0) always wins over intel; intel replaces the optimistic default.
    if "venue_reliability" not in q.model_fields_set:
        intel = _intel_reliability(q.corridor)
        if intel is not None:
            quote.venue_reliability, source = intel
            intel_note = {"venue_reliability": quote.venue_reliability, "source": source}
    result = score_corridor(quote)
    if intel_note:
        result.breakdown["intel"] = intel_note  # provenance in the alert payload
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
