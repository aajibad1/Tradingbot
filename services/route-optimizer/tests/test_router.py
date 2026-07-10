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


def _report(venue, gap_bps, n_fills):
    return {"venues": {venue: {"n_fills": n_fills, "slippage_gap_bps": gap_bps,
                               "avg_realized_slippage_bps": 0.0,
                               "avg_modeled_slippage_bps": 0.0,
                               "total_notional_usd": 0.0}}}


def test_calibration_worsens_a_venue_that_fills_worse_than_modeled():
    # kraken realized 6 bps worse than modeled, with enough fills to trust it.
    from router import apply_calibration
    [c] = apply_calibration([_c("kraken", est_slippage_bps=4.0)], _report("kraken", 6.0, 50))
    assert c.est_slippage_bps == 10.0


def test_calibration_floored_at_zero():
    from router import apply_calibration
    [c] = apply_calibration([_c("kraken", est_slippage_bps=2.0)], _report("kraken", -9.0, 50))
    assert c.est_slippage_bps == 0.0


def test_calibration_ignored_below_sample_threshold():
    from router import apply_calibration
    [c] = apply_calibration([_c("kraken", est_slippage_bps=4.0)], _report("kraken", 6.0, 2))
    assert c.est_slippage_bps == 4.0  # too few fills → priors stand (cold-start)


def test_calibration_leaves_unknown_venue_untouched():
    from router import apply_calibration
    [c] = apply_calibration([_c("newvenue", est_slippage_bps=4.0)], _report("kraken", 6.0, 50))
    assert c.est_slippage_bps == 4.0


def test_calibration_does_not_mutate_inputs():
    from router import apply_calibration
    orig = _c("kraken", est_slippage_bps=4.0)
    apply_calibration([orig], _report("kraken", 6.0, 50))
    assert orig.est_slippage_bps == 4.0


def test_calibration_can_flip_the_recommendation():
    # Two near-identical venues; calibration penalizes the otherwise-better one.
    good = _c("a", est_slippage_bps=4.0)
    other = _c("b", est_slippage_bps=5.0)
    base = rank_routes([good, other])
    assert base.recommended_route == "a"
    # 'a' actually fills 10 bps worse than modeled → 'b' should win.
    report = _report("a", 10.0, 50)
    cal = rank_routes([good, other], route_quality=report)
    assert cal.recommended_route == "b"


def test_none_report_is_noop():
    from router import apply_calibration
    cands = [_c("a")]
    assert apply_calibration(cands, None) is cands


# --- ledger calibration fetch (opt-in via TRADE_LEDGER_URL, fail-soft) ---------

def _client(monkeypatch, get=None):
    import main
    from fastapi.testclient import TestClient
    if get is not None:
        monkeypatch.setattr(main.httpx, "get", get)
    return TestClient(main.app)


def _candidates():
    return [{"venue": "kraken", "size_usd": 10_000, "expected_depth_usd": 500_000,
             "taker_fee_bps": 10, "est_slippage_bps": 2, "latency_ms": 50},
            {"venue": "coinbase", "size_usd": 10_000, "expected_depth_usd": 500_000,
             "taker_fee_bps": 10, "est_slippage_bps": 2, "latency_ms": 50}]


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


def test_omitted_route_quality_fetched_from_ledger(monkeypatch):
    # kraken fills 30bps worse than modeled → after calibration coinbase must win.
    monkeypatch.setenv("TRADE_LEDGER_URL", "http://ledger")
    seen = {}

    def get(url, params=None, timeout=None):
        seen["url"], seen["params"] = url, params
        return _Resp({"venues": {"kraken": {"slippage_gap_bps": 30.0, "n_fills": 40}}})

    client = _client(monkeypatch, get)
    out = client.post("/rank", json={"candidates": _candidates()}).json()
    assert seen["url"] == "http://ledger/ml-export/route-quality"
    assert out["calibrated"] is True
    assert out["recommended_route"] == "coinbase"     # kraken down-ranked by realized gap


def test_explicit_route_quality_skips_fetch(monkeypatch):
    monkeypatch.setenv("TRADE_LEDGER_URL", "http://ledger")

    def _boom(*a, **k):
        raise AssertionError("ledger must not be fetched when route_quality is explicit")

    client = _client(monkeypatch, _boom)
    out = client.post("/rank", json={
        "candidates": _candidates(),
        "route_quality": {"venues": {"coinbase": {"slippage_gap_bps": 30.0, "n_fills": 40}}},
    }).json()
    assert out["calibrated"] is True
    assert out["recommended_route"] == "kraken"       # explicit payload applied


def test_ledger_unreachable_ranks_uncalibrated(monkeypatch):
    monkeypatch.setenv("TRADE_LEDGER_URL", "http://ledger")

    def _down(*a, **k):
        raise RuntimeError("connection refused")

    client = _client(monkeypatch, _down)
    out = client.post("/rank", json={"candidates": _candidates()}).json()
    assert out["calibrated"] is False and out["recommended_route"] is not None


def test_no_env_means_no_fetch(monkeypatch):
    monkeypatch.delenv("TRADE_LEDGER_URL", raising=False)

    def _boom(*a, **k):
        raise AssertionError("must not fetch when env is unset")

    client = _client(monkeypatch, _boom)
    out = client.post("/rank", json={"candidates": _candidates()}).json()
    assert out["calibrated"] is False
