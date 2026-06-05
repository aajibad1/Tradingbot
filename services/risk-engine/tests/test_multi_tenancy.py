"""Multi-tenancy foundation: per-tenant risk-state isolation + platform kill switch."""

from __future__ import annotations

from datetime import datetime

import position_tracker
import state
from rules.kill_switch import is_kill_switch_active, trigger_kill_switch
from state import load_state

from shared.models.trade import Trade, TradeStatus, TradeType
from shared.tenant import risk_key


def _closed_loss(opp_id: str, net: float, user_id: str = "default") -> Trade:
    return Trade(
        id=f"t-{opp_id}", user_id=user_id, opportunity_id=opp_id, type=TradeType.PAPER, legs=[],
        gross_pnl_usd=net, net_pnl_usd=net, status=TradeStatus.CLOSED,
        opened_at=datetime(2026, 1, 1), closed_at=datetime(2026, 1, 1),
    )


def test_tenant_risk_state_is_isolated(fake_redis) -> None:
    fake_redis.set(risk_key("capital_usd", "userA"), "100000")
    fake_redis.set(risk_key("capital_usd", "userB"), "100000")

    position_tracker.apply_trade(fake_redis, _closed_loss("o1", -500.0), "userA")

    # userA moved; userB and the default tenant are untouched.
    assert load_state(fake_redis, "userA").daily_pnl_usd == -500.0
    assert load_state(fake_redis, "userB").daily_pnl_usd == 0.0
    assert fake_redis.get("risk:daily_pnl_usd") is None  # legacy default key never written


def test_apply_trade_routes_by_trade_user_id(fake_redis) -> None:
    # A fill carries its owner; the subscriber routes it to that tenant's state.
    fake_redis.set(risk_key("capital_usd", "u7"), "100000")
    trade = _closed_loss("o9", -250.0, user_id="u7")
    position_tracker.apply_trade(fake_redis, trade, trade.user_id)
    assert load_state(fake_redis, "u7").daily_pnl_usd == -250.0
    assert fake_redis.get("risk:daily_pnl_usd") is None  # default untouched


def test_per_tenant_kill_switch_does_not_leak(fake_redis) -> None:
    trigger_kill_switch(fake_redis, "risk-engine", "drawdown", "userA")
    assert is_kill_switch_active(fake_redis, "userA") is True
    assert is_kill_switch_active(fake_redis, "userB") is False
    assert is_kill_switch_active(fake_redis) is False  # default tenant unaffected


def test_platform_kill_switch_halts_every_tenant(fake_redis) -> None:
    state.set_platform_kill_switch(fake_redis, "admin", "global halt")
    assert is_kill_switch_active(fake_redis, "userA") is True
    assert is_kill_switch_active(fake_redis, "userB") is True
    assert is_kill_switch_active(fake_redis) is True  # default too

    state.clear_platform_kill_switch(fake_redis)
    assert is_kill_switch_active(fake_redis, "userA") is False
