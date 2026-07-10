"""signal-engine — deterministic movement-signal detection (hybrid system).

Emits directional candidate signals from market features. Downstream these become
DIRECTIONAL opportunities gated by the risk-engine directional-sleeve budget — the
signal-engine never executes or bypasses risk.

Endpoints:
  GET  /healthz
  POST /detect   — detect signals from one symbol's features (optionally regime-gated)

Config (env):
  REGIME_CLASSIFIER_URL — opt-in: when set and the request omits ``regime``,
  classify it from the same features via regime-classifier /classify (fail-soft;
  an explicit regime in the request always wins).
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict

import httpx
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
    # Regime-classification inputs (movement-feature-builder emits these too) —
    # only used to auto-classify when ``regime`` is omitted; ignored otherwise.
    trend_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    book_depth_usd: float = Field(default=1_000_000.0, ge=0.0)
    spread_bps: float = Field(default=0.0, ge=0.0)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _classify_regime(f: FeaturesIn) -> str | None:
    """Classify the regime from the same features via regime-classifier (opt-in via
    REGIME_CLASSIFIER_URL). Fail-soft: any error returns None and detection runs
    ungated, exactly as if no classifier were configured — the classifier is
    advisory and must never block signal detection."""
    base = os.environ.get("REGIME_CLASSIFIER_URL")
    if not base:
        return None
    try:
        r = httpx.post(f"{base.rstrip('/')}/classify", json={
            "realized_vol_bps": f.realized_vol_bps,
            "trend_strength": f.trend_strength,
            "book_depth_usd": f.book_depth_usd,
            "spread_bps": f.spread_bps,
            "mean_reversion_z": f.divergence_z,
        }, timeout=3.0)
        return str(r.json()["regime"]) or None
    except Exception:  # noqa: BLE001 — advisory gating must never break /detect
        logger.warning("regime-classifier unavailable; detecting ungated for %s", f.symbol)
        return None


@app.post("/detect")
def detect_signals(f: FeaturesIn) -> dict:
    # An explicit regime in the request always wins; otherwise classify (opt-in).
    regime = f.regime or _classify_regime(f)
    features = MarketFeatures(**f.model_dump(
        exclude={"regime", "trend_strength", "book_depth_usd", "spread_bps"}))
    signals = detect(features, regime)
    if signals:
        logger.info("symbol=%s regime=%s -> %d signal(s): %s", f.symbol, regime,
                    len(signals), ", ".join(s.family.value for s in signals))
    return {"symbol": f.symbol, "regime": regime, "signals": [asdict(s) for s in signals]}
