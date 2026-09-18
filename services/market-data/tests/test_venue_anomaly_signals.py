"""venue_signals()/_push_anomaly_signals(): the feed-health signals market-data
can push to venue-anomaly-detector (opt-in via VENUE_ANOMALY_URL, fail-soft)."""

from __future__ import annotations

import httpx
import pytest

from collectors.base import BaseCollector, CollectorConfig


class _Collector(BaseCollector):
    name = "test"

    async def _build_client(self):
        raise NotImplementedError


def _collector(exchange="kraken") -> _Collector:
    return _Collector(CollectorConfig(exchange=exchange, symbols=["BTC/USD"]), redis=None)


def test_venue_signals_none_before_first_tick():
    c = _collector()
    assert c.venue_signals() is None


def test_venue_signals_computes_spread_and_staleness():
    c = _collector()
    c._last_bid, c._last_ask = 59_990.0, 60_010.0
    c._last_message_at = 1_000.0

    signals = c.venue_signals(now=1_000.25)

    assert signals["venue"] == "kraken"
    assert signals["quote_staleness_ms"] == pytest.approx(250.0)
    mid = (59_990.0 + 60_010.0) / 2.0
    expected_spread_bps = (60_010.0 - 59_990.0) / mid * 10_000.0
    assert signals["spread_bps"] == pytest.approx(expected_spread_bps)


def test_venue_signals_staleness_never_negative():
    c = _collector()
    c._last_bid, c._last_ask = 100.0, 101.0
    c._last_message_at = 1_000.0

    # A clock that appears to move backward must not produce negative staleness.
    signals = c.venue_signals(now=999.0)
    assert signals["quote_staleness_ms"] == 0.0


@pytest.mark.asyncio
async def test_push_anomaly_signals_noop_without_env(monkeypatch):
    monkeypatch.delenv("VENUE_ANOMALY_URL", raising=False)
    c = _collector()
    c._last_bid, c._last_ask = 100.0, 101.0
    # No assertion needed beyond "doesn't raise" — there's nowhere to send it.
    await c._push_anomaly_signals()


@pytest.mark.asyncio
async def test_push_anomaly_signals_noop_before_first_tick(monkeypatch):
    monkeypatch.setenv("VENUE_ANOMALY_URL", "http://127.0.0.1:9")
    c = _collector()
    # No tick yet — must not attempt a network call at all.
    await c._push_anomaly_signals()


@pytest.mark.asyncio
async def test_push_anomaly_signals_posts_to_detect(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json

    monkeypatch.setenv("VENUE_ANOMALY_URL", "http://anomaly.local")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    c = _collector(exchange="crypto.com")
    c._last_bid, c._last_ask = 100.0, 102.0
    c._last_message_at = 5.0

    await c._push_anomaly_signals()

    assert captured["url"] == "http://anomaly.local/detect"
    assert captured["json"]["venue"] == "crypto.com"
    assert "spread_bps" in captured["json"]
    assert "quote_staleness_ms" in captured["json"]


@pytest.mark.asyncio
async def test_push_anomaly_signals_is_fail_soft(monkeypatch):
    class _RaisingAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            raise httpx.ConnectError("unreachable")

    monkeypatch.setenv("VENUE_ANOMALY_URL", "http://anomaly.local")
    monkeypatch.setattr(httpx, "AsyncClient", _RaisingAsyncClient)

    c = _collector()
    c._last_bid, c._last_ask = 100.0, 101.0
    c._last_message_at = 5.0

    await c._push_anomaly_signals()  # must not raise
