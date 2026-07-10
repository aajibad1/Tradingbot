"""route-optimizer — advisory route ranking (hybrid system). Never executes.

Config (env):
  TRADE_LEDGER_URL — opt-in: when set and the request omits ``route_quality``,
  fetch the last-30-days realized-slippage report from trade-ledger
  /ml-export/route-quality and calibrate with it (fail-soft; an explicit
  payload in the request always wins).
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import date, timedelta

import httpx
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
    # When omitted AND TRADE_LEDGER_URL is set, it is fetched from the ledger.
    route_quality: dict | None = None


def _fetch_route_quality() -> dict | None:
    """Fetch the last-30-days route-quality report from trade-ledger — the
    calibration exec_quality.py says it emits for this service. Opt-in
    (TRADE_LEDGER_URL) and fail-soft: any error, non-200, or empty report returns
    None and ranking proceeds uncalibrated, exactly as before — calibration is
    advisory and must never block a ranking."""
    base = os.environ.get("TRADE_LEDGER_URL")
    if not base:
        return None
    end = (date.today() + timedelta(days=1)).isoformat()
    start = (date.today() - timedelta(days=30)).isoformat()
    try:
        r = httpx.get(f"{base.rstrip('/')}/ml-export/route-quality",
                      params={"start": start, "end": end}, timeout=3.0)
        if r.status_code != 200:
            return None
        report = r.json()
    except Exception:  # noqa: BLE001 — advisory calibration must never break /rank
        logger.warning("trade-ledger route-quality unavailable; ranking uncalibrated")
        return None
    return report if report.get("venues") else None


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/rank")
def rank(req: RankRequest) -> dict:
    # An explicit calibration payload always wins; otherwise fetch it (opt-in).
    route_quality = req.route_quality or _fetch_route_quality()
    ranking = rank_routes(
        [RouteCandidate(**c.model_dump()) for c in req.candidates],
        route_quality=route_quality,
    )
    out = asdict(ranking)
    out["calibrated"] = route_quality is not None  # provenance for the caller
    return out
