"""Tests for the sentiment gate.

Fail-OPEN is the headline behaviour: missing data, stale data, transport
errors, and malformed values must all return [] (no violation). Only an
explicit ``sentiment:is_bearish == "1"`` produces a violation.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from rules.sentiment_gate import check_sentiment


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# Unit tests of check_sentiment()                                             #
# --------------------------------------------------------------------------- #


def test_no_sentiment_key_means_no_violation(fake_redis) -> None:
    assert check_sentiment(fake_redis) == []


def test_is_bearish_zero_means_no_violation(fake_redis) -> None:
    fake_redis.set("sentiment:is_bearish", "0")
    assert check_sentiment(fake_redis) == []


def test_is_bearish_one_with_reasons_emits_violation(fake_redis) -> None:
    fake_redis.set("sentiment:is_bearish", "1")
    fake_redis.set(
        "sentiment:block_reasons",
        json.dumps(["fear_greed_index 12 < 25 (Extreme Fear)", "perplexity_score -0.4 < -0.1"]),
    )
    fake_redis.set("sentiment:observed_at", "2026-05-22T12:00:00")

    violations = check_sentiment(fake_redis)
    assert len(violations) == 1
    msg = violations[0].message
    assert "sentiment bearish" in msg
    assert "fear_greed_index 12" in msg
    assert "perplexity_score" in msg
    assert "2026-05-22T12:00:00" in msg


def test_is_bearish_one_without_reasons_still_emits_violation(fake_redis) -> None:
    fake_redis.set("sentiment:is_bearish", "1")
    violations = check_sentiment(fake_redis)
    assert len(violations) == 1
    assert "sentiment bearish" in violations[0].message


def test_garbage_block_reasons_falls_back_gracefully(fake_redis) -> None:
    fake_redis.set("sentiment:is_bearish", "1")
    fake_redis.set("sentiment:block_reasons", "not-json")
    violations = check_sentiment(fake_redis)
    assert len(violations) == 1
    assert "sentiment bearish" in violations[0].message


def test_unexpected_is_bearish_value_fails_open(fake_redis) -> None:
    fake_redis.set("sentiment:is_bearish", "maybe")
    assert check_sentiment(fake_redis) == []


# --------------------------------------------------------------------------- #
# End-to-end: /evaluate integration                                           #
# --------------------------------------------------------------------------- #


def _opp_dict() -> dict:
    from datetime import datetime

    from shared.models.opportunity import Opportunity, StrategyType

    return Opportunity(
        id="o-sentiment",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="hyperliquid",
        gross_spread_bps=120.0,
        trading_fees_bps=31.0,
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=12.0,
        net_edge_bps=75.0,
        confidence_score=0.85,
        recommended_size_usd=1_000.0,
        min_hold_hours=6.0,
        detected_at=datetime.utcnow(),
    ).model_dump(mode="json")


def _seed_health(fake_redis) -> None:
    fake_redis.set("health:latency:kraken", "50")
    fake_redis.set("health:latency:hyperliquid", "50")


def test_evaluate_blocks_on_bearish_sentiment(client, fake_redis) -> None:
    _seed_health(fake_redis)
    fake_redis.set("sentiment:is_bearish", "1")
    fake_redis.set("sentiment:block_reasons", json.dumps(["fear_greed_index 10 < 25"]))

    resp = client.post("/evaluate", json={"opportunity": _opp_dict()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["approved"] is False
    assert any("sentiment bearish" in v["message"] for v in body["violations"])
    # The sentiment gate is a SOFT gate — must NOT trip the kill switch.
    assert body["kill_switch_active"] is False
    assert fake_redis.get("risk:kill_switch:active") is None


def test_evaluate_passes_when_sentiment_neutral(client, fake_redis) -> None:
    _seed_health(fake_redis)
    fake_redis.set("sentiment:is_bearish", "0")

    resp = client.post("/evaluate", json={"opportunity": _opp_dict()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["approved"] is True
    assert body["violations"] == []


def test_evaluate_passes_when_sentiment_keys_missing(client, fake_redis) -> None:
    """Fail-open: if the sentiment refresh never ran, we still trade."""
    _seed_health(fake_redis)
    # No sentiment:* keys at all.
    resp = client.post("/evaluate", json={"opportunity": _opp_dict()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["approved"] is True
