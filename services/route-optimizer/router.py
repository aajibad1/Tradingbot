"""Route optimization (hybrid system, advisory).

Ranks candidate execution routes (which venue/path to fill an order on) by a
transparent score over depth, fees, slippage, latency, and prior fill rate.
ADVISORY — it recommends; the risk-engine still gates and execution still owns
the order. Deterministic first; a learned route-quality model can replace
``score_route`` later with no API change.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# Minimum realized fills on a venue before its calibration is trusted. Below this
# the realized slippage gap is too noisy to act on, so the modeled estimate stands
# (cold-start: a new venue is ranked on its model priors until it has a track record).
MIN_CALIBRATION_FILLS = 5


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


def apply_calibration(
    candidates: list[RouteCandidate], route_quality: dict | None
) -> list[RouteCandidate]:
    """Correct each candidate's ``est_slippage_bps`` from realized fills.

    ``route_quality`` is the report emitted by the trade-ledger
    ``/ml-export/route-quality`` endpoint (``exec_quality.summarize_route_quality``).
    For a venue with at least ``MIN_CALIBRATION_FILLS`` realized fills, the modeled
    estimate is shifted by that venue's realized ``slippage_gap_bps`` (realized −
    modeled) and floored at 0 — closing the loop so the optimizer ranks on observed
    behaviour, not priors. Venues below the sample threshold (or absent from the
    report) are left untouched. Pure: returns a new list, never mutates inputs.
    """
    if not route_quality:
        return candidates
    venues = route_quality.get("venues", {})
    out: list[RouteCandidate] = []
    for c in candidates:
        v = venues.get(c.venue)
        if v and v.get("n_fills", 0) >= MIN_CALIBRATION_FILLS:
            corrected = max(0.0, c.est_slippage_bps + v["slippage_gap_bps"])
            out.append(replace(c, est_slippage_bps=round(corrected, 2)))
        else:
            out.append(c)
    return out


def rank_routes(
    candidates: list[RouteCandidate], route_quality: dict | None = None
) -> RouteRanking:
    """Rank routes, optionally correcting slippage priors with realized calibration."""
    calibrated = apply_calibration(candidates, route_quality)
    ranked = sorted((score_route(c) for c in calibrated), key=lambda r: r.score, reverse=True)
    return RouteRanking(recommended_route=ranked[0].venue if ranked else None, routes=ranked)
