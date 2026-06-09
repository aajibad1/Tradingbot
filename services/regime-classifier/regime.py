"""Market-regime classification (hybrid system, deterministic).

Labels the current market environment from microstructure features so the
signal-engine can gate which families fire (trending → suppress reversion,
mean_reverting → suppress momentum, low_liquidity → suppress imbalance) and the
risk layer can tighten. Rule-based first (transparent, backtestable, no model) —
a clean seam for a learned classifier later.

Single dominant label by priority (risk-off regimes win): low_liquidity →
high_volatility → trending → mean_reverting → ranging. Independent risk flags are
also returned for richer downstream use.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Thresholds (conservative; tune against realized data).
_HIGH_VOL_BPS = 60.0
_MIN_DEPTH_USD = 50_000.0
_WIDE_SPREAD_BPS = 20.0
_TREND_MIN = 0.6       # directional persistence 0..1
_Z_MIN = 2.0           # |divergence z| for mean reversion


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class RegimeFeatures:
    realized_vol_bps: float = 0.0
    trend_strength: float = 0.0      # 0..1 directional persistence
    book_depth_usd: float = 1_000_000.0
    spread_bps: float = 0.0
    mean_reversion_z: float = 0.0    # signed z-score of divergence


@dataclass
class RegimeResult:
    regime: str
    confidence: float
    flags: dict = field(default_factory=dict)


def classify(f: RegimeFeatures) -> RegimeResult:
    high_vol = f.realized_vol_bps > _HIGH_VOL_BPS
    low_liq = f.book_depth_usd < _MIN_DEPTH_USD or f.spread_bps > _WIDE_SPREAD_BPS
    flags = {"high_volatility": high_vol, "low_liquidity": low_liq}

    if low_liq:
        # Confidence grows with how thin/wide the book is.
        depth_short = _clamp(1.0 - f.book_depth_usd / _MIN_DEPTH_USD) if f.book_depth_usd < _MIN_DEPTH_USD else 0.0
        spread_over = _clamp((f.spread_bps - _WIDE_SPREAD_BPS) / _WIDE_SPREAD_BPS) if f.spread_bps > _WIDE_SPREAD_BPS else 0.0
        return RegimeResult("low_liquidity", round(max(0.5, depth_short, spread_over), 4), flags)
    if high_vol:
        return RegimeResult("high_volatility", round(_clamp(f.realized_vol_bps / (2 * _HIGH_VOL_BPS)), 4), flags)
    if f.trend_strength >= _TREND_MIN:
        return RegimeResult("trending", round(_clamp(f.trend_strength), 4), flags)
    if abs(f.mean_reversion_z) >= _Z_MIN:
        return RegimeResult("mean_reverting", round(_clamp(abs(f.mean_reversion_z) / (2 * _Z_MIN)), 4), flags)
    return RegimeResult("ranging", round(1.0 - f.trend_strength, 4), flags)
