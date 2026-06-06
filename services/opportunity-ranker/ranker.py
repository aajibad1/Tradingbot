"""Advisory opportunity ranker (AI plane — Phase B, advisory first).

Predicts whether an opportunity that ALREADY cleared deterministic filtering is
likely to remain profitable after real execution. It is ADVISORY: it ranks and
explains, it does NOT approve — the risk engine remains the final authority
(deterministic rules always run regardless of this score). Fail-open by design:
if ranking is unavailable, deterministic filtering still works.

v1 is a transparent heuristic (no trained model) so it's explainable and a clean
seam for a real model later — inputs and outputs match the brief's schema.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class Features:
    net_edge_bps: float
    size_usd: float = 10_000.0
    top_of_book_depth_usd: float = 0.0
    book_imbalance: float = 0.0          # 0 = balanced, 1 = fully one-sided
    recent_volatility_bps: float = 0.0   # short-window realized vol
    venue_health_score: float = 1.0      # 0..1
    historical_slippage_bps: float = 0.0


@dataclass
class Ranking:
    profitability_probability: float
    expected_net_bps: float
    confidence_score: float
    recommended_action: str              # 'continue_to_risk' | 'deprioritize'
    top_features: list[str] = field(default_factory=list)


def rank(f: Features) -> Ranking:
    # Edge is the dominant signal — logistic centred at the 60 bps "comfortable" mark.
    edge_term = _logistic((f.net_edge_bps - 60.0) / 20.0)
    # Depth relative to order size: thin books make the edge fragile.
    depth_factor = _clamp(f.top_of_book_depth_usd / (2.0 * max(f.size_usd, 1.0)))
    # Volatility erodes persistence; imbalance signals adverse selection.
    vol_factor = _clamp(1.0 - f.recent_volatility_bps / 100.0)
    imbalance_factor = _clamp(1.0 - f.book_imbalance)
    health = _clamp(f.venue_health_score)

    prob = edge_term * (0.4 + 0.6 * depth_factor) * (0.5 + 0.5 * vol_factor) \
        * (0.6 + 0.4 * imbalance_factor) * (0.6 + 0.4 * health)
    prob = _clamp(prob)

    # Expected realized edge after the frictions a backtest can't see.
    expected_net_bps = f.net_edge_bps - f.historical_slippage_bps - 0.5 * f.recent_volatility_bps
    confidence = _clamp(depth_factor * vol_factor * imbalance_factor * health)

    # Explainability: rank the factors by how far each is from ideal (1.0).
    contributions = {
        "net_edge_bps": edge_term,
        "top_of_book_depth": depth_factor,
        "recent_volatility": vol_factor,
        "book_imbalance": imbalance_factor,
        "venue_health_score": health,
    }
    top = sorted(contributions, key=lambda k: contributions[k])[:3]  # weakest first (the drivers)

    return Ranking(
        profitability_probability=round(prob, 4),
        expected_net_bps=round(expected_net_bps, 2),
        confidence_score=round(confidence, 4),
        recommended_action="continue_to_risk" if prob >= 0.5 else "deprioritize",
        top_features=top,
    )
