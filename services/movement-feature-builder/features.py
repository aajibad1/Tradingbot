"""Movement-feature computation (hybrid system, deterministic).

Turns a short window of normalized market observations into the feature set the
signal-engine and regime-classifier consume — closing the gap between ingestion
and the signal layer (features were hand-fed before). Pure functions over a
window; no I/O, fully testable. Produces a superset covering both consumers:

  signal-engine : momentum_bps, realized_vol_bps, venue_lag_bps, book_imbalance,
                  divergence_z, quote_staleness_ms
  regime        : realized_vol_bps, trend_strength, book_depth_usd, spread_bps,
                  mean_reversion_z
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass


@dataclass
class Observation:
    ts_ms: int
    venue: str
    bid: float
    ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass
class MovementFeatures:
    symbol: str
    momentum_bps: float = 0.0
    realized_vol_bps: float = 0.0
    venue_lag_bps: float = 0.0
    book_imbalance: float = 0.0
    divergence_z: float = 0.0
    quote_staleness_ms: float = 0.0
    trend_strength: float = 0.0
    book_depth_usd: float = 0.0
    spread_bps: float = 0.0


def _pct_returns(mids: list[float]) -> list[float]:
    return [(mids[i] / mids[i - 1] - 1.0) for i in range(1, len(mids)) if mids[i - 1] > 0]


def build_features(symbol: str, window: list[Observation], now_ms: int) -> MovementFeatures:
    """Compute features from a time-ordered window of observations (any venues).

    A single lead-venue price path drives momentum/vol/trend; cross-venue spread
    drives venue_lag + divergence z-score. Empty/degenerate windows return zeros.
    """
    if not window:
        return MovementFeatures(symbol=symbol)
    ordered = sorted(window, key=lambda o: o.ts_ms)
    latest = ordered[-1]

    # Lead venue = the one with the most observations (best price path).
    by_venue: dict[str, list[Observation]] = {}
    for o in ordered:
        by_venue.setdefault(o.venue, []).append(o)
    lead = max(by_venue.values(), key=len)
    mids = [o.mid for o in lead if o.mid > 0]

    momentum_bps = ((mids[-1] / mids[0] - 1.0) * 10_000.0) if len(mids) >= 2 and mids[0] > 0 else 0.0
    rets = _pct_returns(mids)
    realized_vol_bps = (statistics.pstdev(rets) * 10_000.0) if len(rets) >= 2 else 0.0
    # Directional persistence: fraction of returns sharing the dominant sign.
    if rets:
        pos = sum(1 for r in rets if r > 0)
        neg = sum(1 for r in rets if r < 0)
        trend_strength = max(pos, neg) / len(rets)
    else:
        trend_strength = 0.0

    # Cross-venue dislocation at the latest snapshot per venue.
    latest_mids = [v[-1].mid for v in by_venue.values() if v[-1].mid > 0]
    venue_lag_bps = ((max(latest_mids) - min(latest_mids)) / min(latest_mids) * 10_000.0) \
        if len(latest_mids) >= 2 and min(latest_mids) > 0 else 0.0

    # Divergence z-score: current cross-venue spread vs the window's spread history.
    spread_hist = _cross_venue_spread_series(ordered)
    if len(spread_hist) >= 2:
        mu = statistics.mean(spread_hist)
        sd = statistics.pstdev(spread_hist)
        divergence_z = ((spread_hist[-1] - mu) / sd) if sd > 0 else 0.0
    else:
        divergence_z = 0.0

    bid_sz, ask_sz = latest.bid_size, latest.ask_size
    book_imbalance = ((bid_sz - ask_sz) / (bid_sz + ask_sz)) if (bid_sz + ask_sz) > 0 else 0.0
    book_depth_usd = (bid_sz + ask_sz) * latest.mid
    spread_bps = ((latest.ask - latest.bid) / latest.mid * 10_000.0) if latest.mid > 0 else 0.0

    return MovementFeatures(
        symbol=symbol,
        momentum_bps=round(momentum_bps, 4),
        realized_vol_bps=round(realized_vol_bps, 4),
        venue_lag_bps=round(venue_lag_bps, 4),
        book_imbalance=round(book_imbalance, 4),
        divergence_z=round(divergence_z, 4),
        quote_staleness_ms=float(max(0, now_ms - latest.ts_ms)),
        trend_strength=round(trend_strength, 4),
        book_depth_usd=round(book_depth_usd, 2),
        spread_bps=round(spread_bps, 4),
    )


def _cross_venue_spread_series(ordered: list[Observation]) -> list[float]:
    """At each timestamp with >= 2 venues quoting, the max−min mid (bps of min)."""
    seen: dict[str, float] = {}
    series: list[float] = []
    for o in ordered:
        if o.mid > 0:
            seen[o.venue] = o.mid
        if len(seen) >= 2:
            lo, hi = min(seen.values()), max(seen.values())
            series.append((hi - lo) / lo * 10_000.0)
    return series


def features_dict(f: MovementFeatures) -> dict:
    return asdict(f)
