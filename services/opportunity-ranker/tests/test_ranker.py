"""Advisory ranker tests — directional, explainable scoring.

Run: PYTHONPATH=.:services/opportunity-ranker python3 -m pytest services/opportunity-ranker/tests/ -v
"""
from __future__ import annotations

from ranker import Features, rank


def _strong(**over):
    base = dict(net_edge_bps=120.0, size_usd=10_000.0, top_of_book_depth_usd=200_000.0,
                book_imbalance=0.0, recent_volatility_bps=5.0, venue_health_score=1.0,
                historical_slippage_bps=4.0)
    base.update(over)
    return Features(**base)


def test_strong_setup_recommends_continue():
    r = rank(_strong())
    assert r.profitability_probability > 0.7
    assert r.recommended_action == "continue_to_risk"
    assert r.expected_net_bps < 120.0  # frictions discount the modeled edge


def test_thin_book_lowers_probability():
    deep = rank(_strong(top_of_book_depth_usd=200_000.0))
    thin = rank(_strong(top_of_book_depth_usd=5_000.0))
    assert thin.profitability_probability < deep.profitability_probability
    assert thin.confidence_score < deep.confidence_score


def test_high_volatility_lowers_probability_and_expected_edge():
    calm = rank(_strong(recent_volatility_bps=5.0))
    wild = rank(_strong(recent_volatility_bps=80.0))
    assert wild.profitability_probability < calm.profitability_probability
    assert wild.expected_net_bps < calm.expected_net_bps


def test_low_edge_deprioritized():
    r = rank(_strong(net_edge_bps=40.0))  # below the comfortable mark
    assert r.recommended_action == "deprioritize"


def test_probability_bounded_and_explained():
    r = rank(_strong())
    assert 0.0 <= r.profitability_probability <= 1.0
    assert 0.0 <= r.confidence_score <= 1.0
    assert len(r.top_features) == 3
