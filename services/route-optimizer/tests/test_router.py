from __future__ import annotations
from router import RouteCandidate, rank_routes, score_route


def _c(venue, **o):
    base = dict(venue=venue, size_usd=10_000.0, expected_depth_usd=200_000.0,
                taker_fee_bps=10.0, est_slippage_bps=4.0, latency_ms=50.0, prior_fill_rate=1.0)
    base.update(o)
    return RouteCandidate(**base)


def test_deeper_book_ranks_higher():
    deep, thin = score_route(_c("a")), score_route(_c("b", expected_depth_usd=5_000.0))
    assert deep.score > thin.score
    assert deep.predicted_fill_quality > thin.predicted_fill_quality


def test_cheaper_route_ranks_higher():
    cheap, pricey = score_route(_c("a", taker_fee_bps=5)), score_route(_c("b", taker_fee_bps=60))
    assert cheap.score > pricey.score


def test_lower_latency_ranks_higher():
    fast, slow = score_route(_c("a", latency_ms=20)), score_route(_c("b", latency_ms=900))
    assert fast.score > slow.score


def test_rank_recommends_best_and_sorts():
    r = rank_routes([_c("cheap_deep"), _c("thin", expected_depth_usd=3_000.0),
                     _c("pricey", taker_fee_bps=80.0)])
    assert r.recommended_route == "cheap_deep"
    scores = [x.score for x in r.routes]
    assert scores == sorted(scores, reverse=True)


def test_empty_candidates():
    r = rank_routes([])
    assert r.recommended_route is None and r.routes == []
