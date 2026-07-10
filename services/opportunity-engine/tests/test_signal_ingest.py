"""POST /signal — signal-engine movement signals become DIRECTIONAL opportunities.

The endpoint converts via directional.opportunity_from_signal, applies the same
publish threshold as the neutral strategies, and publishes to arb-opportunities —
risk-engine (directional sleeve, 0 by default) remains the only gate.
"""
from __future__ import annotations

import main as engine_main
from fastapi.testclient import TestClient
from shared.models.opportunity import StrategyType
from shared.pubsub.publisher import Topic


class _CapturePublisher:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, attributes=None):
        self.published.append((topic, payload, attributes))
        return "id"


def _client(monkeypatch):
    pub = _CapturePublisher()
    monkeypatch.setattr(engine_main, "get_publisher", lambda: pub)
    return TestClient(engine_main.app), pub


def _signal(**over):
    # 500 bps expected move — comfortably above threshold after round-trip costs.
    base = dict(symbol="BTC/USD:PERP", direction="long", gross_edge_bps=500.0,
                confidence=0.8, expiry_ms=3_600_000)
    base.update(over)
    return base


def test_strong_signal_published_as_directional(monkeypatch):
    client, pub = _client(monkeypatch)
    r = client.post("/signal", json=_signal())
    body = r.json()
    assert r.status_code == 200 and body["published"] is True
    topic, opp, attrs = pub.published[0]
    assert topic == Topic.OPPORTUNITIES
    assert opp.strategy == StrategyType.DIRECTIONAL and opp.direction == "long"
    assert opp.execute is False               # risk-engine remains the gate
    assert attrs == {"strategy": "directional", "asset": "BTC"}
    assert body["net_edge_bps"] == round(opp.net_edge_bps, 2)


def test_weak_signal_not_published(monkeypatch):
    client, pub = _client(monkeypatch)
    r = client.post("/signal", json=_signal(gross_edge_bps=20.0))  # eaten by costs
    body = r.json()
    assert r.status_code == 200 and body["published"] is False
    assert body["net_edge_bps"] < body["threshold_bps"]
    assert pub.published == []                # below the bar → never leaves the engine


def test_bad_direction_is_422(monkeypatch):
    client, pub = _client(monkeypatch)
    assert client.post("/signal", json=_signal(direction="sideways")).status_code == 422
    assert pub.published == []


def test_unknown_venue_fails_loud(monkeypatch):
    # taker_fee_bps raises KeyError on an unknown venue — surfaced as 422,
    # never a silently-defaulted fee.
    client, pub = _client(monkeypatch)
    assert client.post("/signal", json=_signal(venue="mtgox")).status_code == 422
    assert pub.published == []
