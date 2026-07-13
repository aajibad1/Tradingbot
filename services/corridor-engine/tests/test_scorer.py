"""Corridor scorer tests — the Africa corridor net-edge + settlement-risk model.

Run: PYTHONPATH=.:services/corridor-engine python3 -m pytest services/corridor-engine/tests/ -v
"""
from __future__ import annotations

from scorer import (
    MIN_CORRIDOR_NET_EDGE_BPS,
    CorridorQuote,
    score_corridor,
)


def _quote(**over) -> CorridorQuote:
    # Baseline: crypto route yields 2% more dest currency than the official FX —
    # a 200 bps gross dislocation before costs.
    base = dict(
        corridor="NGN->ZAR",
        base_asset="USDT",
        amount_source=1_000_000.0,
        stablecoin_per_source=0.00061,        # USDT per NGN at buy venue
        dest_per_stablecoin=18.36,            # ZAR per USDT at sell venue
        official_fx_dest_per_source=0.010980,  # ZAR per NGN benchmark (gives ~+2%)
    )
    base.update(over)
    return CorridorQuote(**base)


def test_clean_corridor_is_viable():
    s = score_corridor(_quote(buy_fee_bps=10, sell_fee_bps=10, conversion_cost_bps=20))
    assert s.gross_edge_bps > 150
    assert s.net_edge_bps > MIN_CORRIDOR_NET_EDGE_BPS
    assert s.viable is True


def test_costs_can_eat_the_edge():
    # Heavy ramp + payout friction pushes net below the bar.
    s = score_corridor(_quote(buy_fee_bps=40, sell_fee_bps=40, conversion_cost_bps=80,
                              payout_friction_bps=60))
    assert s.net_edge_bps < MIN_CORRIDOR_NET_EDGE_BPS
    assert s.viable is False


def test_settlement_risk_reduces_net_edge():
    fast = score_corridor(_quote(settlement_time_hours=1, fx_volatility_bps_per_hour=5))
    slow = score_corridor(_quote(settlement_time_hours=20, fx_volatility_bps_per_hour=5))
    assert slow.settlement_risk_bps > fast.settlement_risk_bps
    assert slow.net_edge_bps < fast.net_edge_bps


def test_low_settlement_confidence_blocks_even_with_edge():
    # Big edge, but a long settlement window + flaky rail → confidence below floor.
    s = score_corridor(_quote(settlement_time_hours=72, venue_reliability=0.4))
    assert s.settlement_confidence < 0.5
    assert s.viable is False  # never alert when we can't trust settlement


def test_negative_dislocation_is_not_viable():
    # crypto route is WORSE than official FX → negative gross edge
    s = score_corridor(_quote(official_fx_dest_per_source=0.012))
    assert s.gross_edge_bps < 0
    assert s.viable is False


# --- alert publishing (alert-only path) ---------------------------------------

class _CapturePublisher:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, attributes=None):
        self.published.append((topic, payload, attributes))
        return "id"


def _client(monkeypatch):
    import main
    from fastapi.testclient import TestClient
    pub = _CapturePublisher()
    monkeypatch.setattr(main, "_get_publisher", lambda: pub)
    return TestClient(main.app), pub


def _quote_json(**over):
    base = dict(corridor="NGN->ZAR", base_asset="USDT", amount_source=1_000_000.0,
                stablecoin_per_source=0.00061, dest_per_stablecoin=18.36,
                official_fx_dest_per_source=0.010980, buy_fee_bps=10, sell_fee_bps=10,
                conversion_cost_bps=20)
    base.update(over)
    return base


def test_viable_corridor_publishes_alert(monkeypatch):
    from shared.pubsub.publisher import Topic
    client, pub = _client(monkeypatch)
    r = client.post("/score", json=_quote_json())
    assert r.status_code == 200 and r.json()["viable"] is True
    assert len(pub.published) == 1
    assert pub.published[0][0] == Topic.CORRIDOR_ALERTS


def test_nonviable_corridor_does_not_publish(monkeypatch):
    client, pub = _client(monkeypatch)
    r = client.post("/score", json=_quote_json(official_fx_dest_per_source=0.012))
    assert r.status_code == 200 and r.json()["viable"] is False
    assert pub.published == []


# --- intel enrichment (corridor-intelligence-service, opt-in + fail-soft) ------

class _IntelResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_intel_fills_omitted_reliability_and_can_demote(monkeypatch):
    # Caller omits venue_reliability (would default to an optimistic 1.0); intel
    # says the rail is flaky (0.4) → confidence below floor → NOT viable.
    import main
    monkeypatch.setenv("CORRIDOR_INTEL_URL", "http://intel")
    monkeypatch.setattr(main.httpx, "post", lambda url, json=None, timeout=None:
                        _IntelResp({"reliability_score": 0.4, "source": "perplexity"}))
    client, pub = _client(monkeypatch)
    r = client.post("/score", json=_quote_json()).json()
    assert r["settlement_confidence"] == 0.4
    assert r["viable"] is False           # the 1.0 default would have alerted
    assert r["breakdown"]["intel"] == {"venue_reliability": 0.4, "source": "perplexity"}
    assert pub.published == []


def test_explicit_reliability_wins_over_intel(monkeypatch):
    # An explicit caller value — even 1.0 — means intel is never consulted.
    import main
    monkeypatch.setenv("CORRIDOR_INTEL_URL", "http://intel")

    def _boom(*a, **k):
        raise AssertionError("intel must not be consulted when reliability is explicit")

    monkeypatch.setattr(main.httpx, "post", _boom)
    client, _ = _client(monkeypatch)
    r = client.post("/score", json=_quote_json(venue_reliability=1.0)).json()
    assert r["viable"] is True and "intel" not in r["breakdown"]


def test_intel_unreachable_is_fail_soft(monkeypatch):
    import main
    monkeypatch.setenv("CORRIDOR_INTEL_URL", "http://intel")

    def _down(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(main.httpx, "post", _down)
    client, _ = _client(monkeypatch)
    r = client.post("/score", json=_quote_json()).json()
    assert r["viable"] is True            # scored with the quote as given
    assert "intel" not in r["breakdown"]  # no provenance claimed


def test_scan_ranks_viable_and_skips_nonviable(monkeypatch):
    client, pub = _client(monkeypatch)
    quotes = [
        _quote_json(corridor="NGN->ZAR"),                                  # viable, ~big edge
        _quote_json(corridor="KES->ZAR", dest_per_stablecoin=18.20),       # viable, smaller edge
        _quote_json(corridor="GHS->NGN", official_fx_dest_per_source=0.012),  # not viable
    ]
    r = client.post("/scan", json={"quotes": quotes}).json()
    assert r["scanned"] == 3
    corridors = [v["corridor"] for v in r["viable"]]
    assert "GHS->NGN" not in corridors
    # ranked by net edge descending
    edges = [v["net_edge_bps"] for v in r["viable"]]
    assert edges == sorted(edges, reverse=True)
    # only the viable ones were alerted
    assert len(pub.published) == len(r["viable"])


# --- FX benchmark fill (fx-rate-service; load-bearing → fail loud) --------------

def test_omitted_benchmark_computed_from_fx_rates(monkeypatch):
    # NGN 1600/USD, ZAR 18/USD → ZAR per NGN = 0.01125; crypto route gives ~2% more.
    import main
    monkeypatch.setenv("FX_RATE_SERVICE_URL", "http://fx")

    class _R(_IntelResp):
        def raise_for_status(self):
            pass

    monkeypatch.setattr(main.httpx, "get",
                        lambda url, params=None, timeout=None:
                        _R({"rate": {"NGN": 1600.0, "ZAR": 18.0}[params["currency"]]}))
    client, _ = _client(monkeypatch)
    q = _quote_json()
    q.pop("official_fx_dest_per_source", None)
    q["stablecoin_per_source"] = 0.000625        # USDT per NGN
    q["dest_per_stablecoin"] = 18.36             # ZAR per USDT → ~2% over 0.01125
    r = client.post("/score", json=q)
    body = r.json()
    assert r.status_code == 200
    assert body["breakdown"]["fx_benchmark"]["official_fx_dest_per_source"] == 0.01125
    assert body["breakdown"]["fx_benchmark"]["source"] == "fx-rate-service:official"
    assert body["gross_edge_bps"] > 150          # edge measured against the fetched benchmark


def test_omitted_benchmark_without_fx_env_is_422(monkeypatch):
    monkeypatch.delenv("FX_RATE_SERVICE_URL", raising=False)
    client, pub = _client(monkeypatch)
    q = _quote_json()
    q.pop("official_fx_dest_per_source", None)
    r = client.post("/score", json=q)
    assert r.status_code == 422 and "FX benchmark" in r.json()["detail"]
    assert pub.published == []                    # nothing nonsensical alerted


def test_fx_service_down_is_503_not_silent_zero(monkeypatch):
    import main
    monkeypatch.setenv("FX_RATE_SERVICE_URL", "http://fx")

    def _down(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(main.httpx, "get", _down)
    client, pub = _client(monkeypatch)
    q = _quote_json()
    q.pop("official_fx_dest_per_source", None)
    r = client.post("/score", json=q)
    assert r.status_code == 503 and pub.published == []


def test_explicit_benchmark_skips_fx_fetch(monkeypatch):
    import main
    monkeypatch.setenv("FX_RATE_SERVICE_URL", "http://fx")

    def _boom(*a, **k):
        raise AssertionError("fx must not be fetched when the benchmark is explicit")

    monkeypatch.setattr(main.httpx, "get", _boom)
    client, _ = _client(monkeypatch)
    r = client.post("/score", json=_quote_json())   # carries the explicit benchmark
    assert r.status_code == 200 and "fx_benchmark" not in r.json()["breakdown"]


def test_scan_publishes_nothing_when_any_quote_fails_to_prepare(monkeypatch):
    # A bad quote mid-batch must abort the WHOLE scan before any alert publishes —
    # a retried scan would otherwise duplicate the earlier quotes' alerts.
    monkeypatch.delenv("FX_RATE_SERVICE_URL", raising=False)
    client, pub = _client(monkeypatch)
    good = _quote_json()                       # viable, explicit benchmark
    bad = _quote_json()
    bad.pop("official_fx_dest_per_source")     # no benchmark, no FX env → 422
    r = client.post("/scan", json={"quotes": [good, bad]})
    assert r.status_code == 422
    assert pub.published == []                 # the viable quote did NOT alert
