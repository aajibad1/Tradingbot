"""signal-engine — deterministic movement-signal detection (hybrid system).

Emits directional candidate signals from market features. Downstream these become
DIRECTIONAL opportunities gated by the risk-engine directional-sleeve budget — the
signal-engine never executes or bypasses risk.

Endpoints:
  GET  /healthz
  POST /detect   — detect signals from one symbol's features (optionally regime-gated)
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel, Field

from signals import MarketFeatures, detect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("signal-engine")

app = FastAPI(title="signal-engine", version="0.1.0")


class FeaturesIn(BaseModel):
    symbol: str
    momentum_bps: float = 0.0
    realized_vol_bps: float = Field(default=0.0, ge=0.0)
    venue_lag_bps: float = Field(default=0.0, ge=0.0)
    book_imbalance: float = Field(default=0.0, ge=-1.0, le=1.0)
    divergence_z: float = 0.0
    quote_staleness_ms: float = Field(default=0.0, ge=0.0)
    regime: str | None = None


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/detect")
def detect_signals(f: FeaturesIn) -> dict:
    regime = f.regime
    features = MarketFeatures(**f.model_dump(exclude={"regime"}))
    signals = detect(features, regime)
    if signals:
        logger.info("symbol=%s regime=%s -> %d signal(s): %s", f.symbol, regime,
                    len(signals), ", ".join(s.family.value for s in signals))
    return {"symbol": f.symbol, "regime": regime, "signals": [asdict(s) for s in signals]}
