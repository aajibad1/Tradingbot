"""Route optimization (hybrid system, advisory).

Ranks candidate execution routes (which venue/path to fill an order on) by a
transparent score over depth, fees, slippage, latency, and prior fill rate.
ADVISORY — it recommends; the risk-engine still gates and execution still owns
the order. Deterministic first; a learned route-quality model can replace
``score_route`` later with no API change.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RouteCandidate:
    venue: str
    size_usd: float
    expected_depth_usd: float = 0.0
    taker_fee_bps: float = 0.0
    est_slippage_bps: float = 0.0
    latency_ms: float = 0.0
    prior_fill_rate: float = 1.0   # 0..1 historical fill success


@dataclass
class RankedRoute:
    venue: str
    score: float
    predicted_fill_quality: float   # 0..1
    predicted_slippage_bps: float
    cost_bps: float


@dataclass
class RouteRanking:
    recommended_route: str | None
    routes: list[RankedRoute] = field(default_factory=list)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_route(c: RouteCandidate) -> RankedRoute:
    # Depth relative to order size → fill quality; thin books fill worse.
    depth_factor = _clamp(c.expected_depth_usd / (2.0 * max(c.size_usd, 1.0)))
    fill_quality = _clamp(c.prior_fill_rate * (0.4 + 0.6 * depth_factor))
    # Total cost in bps (paid), and a latency penalty (slow routes miss the move).
    cost_bps = c.taker_fee_bps + c.est_slippage_bps
    latency_penalty = _clamp(c.latency_ms / 1000.0)  # 1s ≈ full penalty
    # Higher score is better: reward fill quality, penalize cost + latency.
    score = fill_quality - cost_bps / 100.0 - 0.2 * latency_penalty
    return RankedRoute(
        venue=c.venue, score=round(score, 4),
        predicted_fill_quality=round(fill_quality, 4),
        predicted_slippage_bps=round(c.est_slippage_bps, 2),
        cost_bps=round(cost_bps, 2),
    )


def rank_routes(candidates: list[RouteCandidate]) -> RouteRanking:
    ranked = sorted((score_route(c) for c in candidates), key=lambda r: r.score, reverse=True)
    return RouteRanking(recommended_route=ranked[0].venue if ranked else None, routes=ranked)
