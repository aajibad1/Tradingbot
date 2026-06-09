"""Movement-signal detection (hybrid system, deterministic layer).

Detects directional setups from market microstructure features and emits candidate
signals with an expiry. These are NON-neutral (directional) — downstream they
become DIRECTIONAL opportunities that the risk-engine admits only within the
directional-sleeve budget. The deterministic detectors decide *what* is a setup;
AI (opportunity-ranker) ranks/suppresses; the risk-engine owns the hard caps.

Each detector is a transparent threshold rule (no model) so it's explainable and
backtestable — a clean seam for learned detectors later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class SignalFamily(str, Enum):
    MOMENTUM_DISLOCATION = "momentum_dislocation"
    ORDERBOOK_IMBALANCE = "orderbook_imbalance"
    STAT_ARB_REVERSION = "stat_arb_reversion"


# Thresholds (bps / ratios). Conservative on purpose — directional is the
# higher-risk sleeve, so we'd rather miss setups than fire on noise.
_MOMENTUM_MIN_BPS = 30.0       # short-window move to call it momentum
_LAG_MIN_BPS = 8.0             # uncaptured propagation on a lagging venue
_IMBALANCE_MIN = 0.6          # |book imbalance| (−1..1) to call it pressure
_MAX_STALENESS_MS = 750.0     # ignore microstructure on stale quotes
_Z_MIN = 2.0                  # |z-score| of divergence to expect reversion
_MIN_CONFIDENCE = 0.4         # below this we don't emit


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class MarketFeatures:
    symbol: str
    momentum_bps: float = 0.0        # signed short-window return
    realized_vol_bps: float = 0.0    # short-window realized vol
    venue_lag_bps: float = 0.0       # how far a lagging venue trails the move (>=0)
    book_imbalance: float = 0.0      # −1..1, + = bid-heavy (buy pressure)
    divergence_z: float = 0.0        # signed z-score of spread vs its mean
    quote_staleness_ms: float = 0.0


@dataclass
class Signal:
    symbol: str
    family: SignalFamily
    direction: Direction
    gross_edge_bps: float
    confidence: float
    expiry_ms: int
    regime_hint: str | None = None
    features: dict = field(default_factory=dict)


def _momentum(f: MarketFeatures) -> Signal | None:
    if abs(f.momentum_bps) < _MOMENTUM_MIN_BPS or f.venue_lag_bps < _LAG_MIN_BPS:
        return None
    # Volatility that dwarfs the move makes the lag likely to be noise, not signal.
    vol_factor = _clamp(1.0 - f.realized_vol_bps / max(abs(f.momentum_bps), 1.0))
    confidence = _clamp(abs(f.momentum_bps) / 100.0 * (0.5 + 0.5 * vol_factor))
    return Signal(
        symbol=f.symbol, family=SignalFamily.MOMENTUM_DISLOCATION,
        direction=Direction.LONG if f.momentum_bps > 0 else Direction.SHORT,
        gross_edge_bps=f.venue_lag_bps, confidence=round(confidence, 4),
        expiry_ms=2000, regime_hint="trending",
        features={"momentum_bps": f.momentum_bps, "venue_lag_bps": f.venue_lag_bps},
    )


def _imbalance(f: MarketFeatures) -> Signal | None:
    if abs(f.book_imbalance) < _IMBALANCE_MIN or f.quote_staleness_ms > _MAX_STALENESS_MS:
        return None
    return Signal(
        symbol=f.symbol, family=SignalFamily.ORDERBOOK_IMBALANCE,
        direction=Direction.LONG if f.book_imbalance > 0 else Direction.SHORT,
        gross_edge_bps=round(abs(f.book_imbalance) * 12.0, 2),  # short-lived, small edge
        confidence=round(_clamp(abs(f.book_imbalance)), 4),
        expiry_ms=800, regime_hint="high_volatility",
        features={"book_imbalance": f.book_imbalance},
    )


def _reversion(f: MarketFeatures) -> Signal | None:
    if abs(f.divergence_z) < _Z_MIN:
        return None
    # Spread too HIGH (z>0) → expect it to fall → SHORT the spread; vice versa.
    return Signal(
        symbol=f.symbol, family=SignalFamily.STAT_ARB_REVERSION,
        direction=Direction.SHORT if f.divergence_z > 0 else Direction.LONG,
        gross_edge_bps=round(abs(f.divergence_z) * 6.0, 2),
        confidence=round(_clamp(abs(f.divergence_z) / 4.0), 4),
        expiry_ms=15000, regime_hint="mean_reverting",
        features={"divergence_z": f.divergence_z},
    )


_DETECTORS = (_momentum, _imbalance, _reversion)

# Regime gating: in a given regime, suppress families that fight it.
_REGIME_SUPPRESS = {
    "trending": {SignalFamily.STAT_ARB_REVERSION},
    "mean_reverting": {SignalFamily.MOMENTUM_DISLOCATION},
    "low_liquidity": {SignalFamily.ORDERBOOK_IMBALANCE},  # thin books → unreliable
}


def detect(f: MarketFeatures, regime: str | None = None) -> list[Signal]:
    """Run all detectors, drop low-confidence and regime-incompatible signals,
    return strongest first."""
    suppressed = _REGIME_SUPPRESS.get(regime or "", set())
    out = []
    for det in _DETECTORS:
        s = det(f)
        if s is None or s.confidence < _MIN_CONFIDENCE or s.family in suppressed:
            continue
        if regime:
            s.regime_hint = regime
        out.append(s)
    return sorted(out, key=lambda s: s.confidence, reverse=True)
