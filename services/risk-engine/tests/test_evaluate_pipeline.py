"""End-to-end tests of the /evaluate pipeline.

These exercise the five-stage flow defined in ``main.py``:
  1. Kill switch — short-circuits.
  2. Position limits.
  3. Drawdown — trips the kill switch synchronously inside the handler.
  4. Liquidation distance.
  5. Exchange health.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from shared.models.opportunity import Opportunity, StrategyType


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# 75-bp net is comfortably above the 50-bp MIN_NET_EDGE bar.
_DEFAULT_NET_EDGE_BPS = 75.0


def _opp(net_edge_bps: float = _DEFAULT_NET_EDGE_BPS, size_usd: float = 1_000.0) -> dict:
    return Opportunity(
        id="o-pipeline",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="hyperliquid",
        gross_spread_bps=120.0,
        trading_fees_bps=31.0,
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=12.0,
        net_edge_bps=net_edge_bps,
        confidence_score=0.85,
        recommended_size_usd=size_usd,
        min_hold_hours=6.0,
        detected_at=datetime.utcnow(),
    ).model_dump(mode="json")


def _seed_health(fake_redis, latency_ms: float = 50.0) -> None:
    fake_redis.set("health:latency:kraken", str(latency_ms))
    fake_redis.set("health:latency:hyperliquid", str(latency_ms))


def test_kill_switch_blocks_all_evaluation(client, fake_redis) -> None:
    _seed_health(fake_redis)
    from rules.kill_switch import trigger_kill_switch

    trigger_kill_switch(fake_redis, "test", "manual lock")

    resp = client.post("/evaluate", json={"opportunity": _opp()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["approved"] is False
    assert body["kill_switch_active"] is True
    # When the kill switch short-circuits no other rules run, so violations
    # is an empty list — the kill switch itself is the rejection reason.
    assert body["violations"] == []


def test_healthy_opportunity_approved(client, fake_redis) -> None:
    _seed_health(fake_redis, latency_ms=50.0)
    resp = client.post("/evaluate", json={"opportunity": _opp()})
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["approved"] is True, body
    assert body["violations"] == []


def test_drawdown_breach_trips_kill_switch_synchronously(client, fake_redis) -> None:
    _seed_health(fake_redis)
    # Capital 100K, daily PnL -2K = 2% loss → exceeds 1% limit.
    fake_redis.set("risk:daily_pnl_usd", "-2000")

    resp = client.post("/evaluate", json={"opportunity": _opp()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["approved"] is False
    # Same request reports both the violation AND that the switch is now on.
    rules = [v["rule"] for v in body["violations"]]
    assert "daily_loss_limit" in rules
    assert body["kill_switch_active"] is True
    assert fake_redis.get("risk:kill_switch:active") == "1"


def test_exchange_health_rejection(client, fake_redis) -> None:
    fake_redis.set("health:latency:kraken", "750")  # > 500ms limit
    fake_redis.set("health:latency:hyperliquid", "60")

    resp = client.post("/evaluate", json={"opportunity": _opp()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["approved"] is False
    assert any(v["rule"] == "api_latency" for v in body["violations"])


def test_missing_health_signal_fails_closed(client, fake_redis) -> None:
    # No health:latency:* keys at all.
    resp = client.post("/evaluate", json={"opportunity": _opp()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["approved"] is False
    assert any(v["rule"] == "api_latency" for v in body["violations"])


def test_low_net_edge_rejected(client, fake_redis) -> None:
    _seed_health(fake_redis)
    resp = client.post("/evaluate", json={"opportunity": _opp(net_edge_bps=5.0)})
    body = resp.json()
    assert resp.status_code == 200
    assert body["approved"] is False
    assert any(v["rule"] == "min_net_edge" for v in body["violations"])


def test_capital_unset_raises_loud(client, fake_redis) -> None:
    fake_redis.delete("risk:capital_usd")
    _seed_health(fake_redis)

    resp = client.post("/evaluate", json={"opportunity": _opp()})
    # ``load_state`` raises RuntimeError; FastAPI converts to 500.
    assert resp.status_code == 500


def test_healthz_returns_503_when_kill_switch_active(client, fake_redis) -> None:
    from rules.kill_switch import trigger_kill_switch

    trigger_kill_switch(fake_redis, "test", "manual")
    resp = client.get("/healthz")
    assert resp.status_code == 503


def test_admin_reset_requires_correct_token(client, fake_redis, monkeypatch) -> None:
    monkeypatch.setenv("KILL_SWITCH_RESET_TOKEN", "secret-token")
    from rules.kill_switch import trigger_kill_switch

    trigger_kill_switch(fake_redis, "test", "manual")

    # Wrong token rejected.
    bad = client.post(
        "/admin/reset",
        json={"auth_token": "nope", "reset_by": "ops"},
    )
    assert bad.status_code == 403
    assert fake_redis.get("risk:kill_switch:active") == "1"

    # Correct token resets.
    ok = client.post(
        "/admin/reset",
        json={"auth_token": "secret-token", "reset_by": "ops"},
    )
    assert ok.status_code == 200
    assert ok.json()["active"] is False
    assert fake_redis.get("risk:kill_switch:active") is None


def test_liquidation_warning_band_records_violation(client, fake_redis) -> None:
    """A position 10% from liquidation should add a warn-level violation but
    NOT trip the kill switch."""
    _seed_health(fake_redis)
    fake_redis.hset(
        "risk:positions:open",
        "opp-1",
        json.dumps(
            {
                "opportunity_id": "opp-1",
                "exchange": "hyperliquid",
                "asset": "BTC",
                "mark_price": 60_000.0,
                "liquidation_price": 54_000.0,  # 10% away → warn band
            }
        ),
    )
    resp = client.post("/evaluate", json={"opportunity": _opp()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["approved"] is False  # any violation blocks approval
    assert any("liquidation" in v["message"] for v in body["violations"])
    assert body["kill_switch_active"] is False


def test_liquidation_critical_band_trips_kill_switch(client, fake_redis) -> None:
    """A position inside the 5% critical band must trip the kill switch."""
    _seed_health(fake_redis)
    fake_redis.hset(
        "risk:positions:open",
        "opp-2",
        json.dumps(
            {
                "opportunity_id": "opp-2",
                "exchange": "hyperliquid",
                "asset": "BTC",
                "mark_price": 60_000.0,
                "liquidation_price": 58_500.0,  # 2.5% away → critical
            }
        ),
    )
    resp = client.post("/evaluate", json={"opportunity": _opp()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["kill_switch_active"] is True
    assert fake_redis.get("risk:kill_switch:active") == "1"


def test_liquidations_endpoint_returns_scan(client, fake_redis) -> None:
    fake_redis.hset(
        "risk:positions:open",
        "opp-3",
        json.dumps(
            {
                "opportunity_id": "opp-3",
                "exchange": "hyperliquid",
                "asset": "ETH",
                "mark_price": 3_000.0,
                "liquidation_price": 2_700.0,  # 10% → warn
            }
        ),
    )
    resp = client.get("/liquidations")
    body = resp.json()
    assert resp.status_code == 200
    assert len(body["positions"]) == 1
    pos = body["positions"][0]
    assert pos["opportunity_id"] == "opp-3"
    assert pos["severity"] == "warn"
