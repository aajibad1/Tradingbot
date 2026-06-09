"""Hybrid directional-sleeve budget: neutral always allowed; directional capped."""

from __future__ import annotations

from datetime import datetime

import position_tracker
from rules.directional_budget import check_directional_budget
from rules.drawdown_guard import DEFAULT_LIMITS
from state import load_state

from shared.models.opportunity import Opportunity, StrategyType
from shared.models.trade import Trade, TradeLeg, TradeStatus, TradeType


def _opp(strategy: StrategyType, size_usd: float = 1_000.0) -> Opportunity:
    return Opportunity(
        id="o-dir", strategy=strategy, asset="BTC",
        long_exchange="kraken", short_exchange="hyperliquid",
        gross_spread_bps=120.0, trading_fees_bps=31.0, slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=12.0, net_edge_bps=75.0, confidence_score=0.85,
        recommended_size_usd=size_usd, min_hold_hours=6.0, detected_at=datetime(2026, 1, 1),
    )


def _state(fake_redis):
    return load_state(fake_redis, "default")  # fixture seeds risk:capital_usd=100000


def test_neutral_opportunity_never_uses_directional_budget(fake_redis):
    # budget is 0 by default, yet a neutral (funding-arb) opp is not gated here
    assert check_directional_budget(_opp(StrategyType.FUNDING_RATE_ARB), _state(fake_redis), DEFAULT_LIMITS) == []


def test_directional_rejected_when_budget_zero(fake_redis):
    v = check_directional_budget(_opp(StrategyType.DIRECTIONAL), _state(fake_redis), DEFAULT_LIMITS)
    assert len(v) == 1 and v[0].rule.value == "directional_budget"


def test_directional_allowed_within_budget(fake_redis):
    limits = {**DEFAULT_LIMITS, "max_directional_exposure_pct": 10.0}
    # $1k on $100k = 1% directional, under the 10% budget
    assert check_directional_budget(_opp(StrategyType.DIRECTIONAL), _state(fake_redis), limits) == []


def test_directional_rejected_over_budget(fake_redis):
    limits = {**DEFAULT_LIMITS, "max_directional_exposure_pct": 5.0}
    v = check_directional_budget(_opp(StrategyType.DIRECTIONAL, size_usd=8_000.0), _state(fake_redis), limits)
    assert len(v) == 1  # 8% projected > 5% budget


def _dir_trade(status: TradeStatus, notional: float = 5_000.0) -> Trade:
    qty = notional / 50_000.0
    return Trade(
        id=f"t-{status.value}", opportunity_id="o-dir", type=TradeType.PAPER,
        legs=[TradeLeg(exchange="kraken", side="buy", asset="BTC", size=qty,
                       fill_price=50_000.0, fee_usd=5, slippage_usd=2, filled_at=datetime(2026, 1, 1))],
        gross_pnl_usd=0.0, net_pnl_usd=0.0, status=status,
        opened_at=datetime(2026, 1, 1), directional=True,
    )


def test_directional_open_then_close_tracks_exposure(fake_redis):
    # open a $5k directional position on $100k capital → +5% directional exposure
    position_tracker.apply_trade(fake_redis, _dir_trade(TradeStatus.OPEN), "default")
    assert abs(load_state(fake_redis, "default").directional_exposure_pct - 5.0) < 1e-6
    # closing it releases the exposure back toward 0
    position_tracker.apply_trade(fake_redis, _dir_trade(TradeStatus.CLOSED), "default")
    assert load_state(fake_redis, "default").directional_exposure_pct == 0.0


def test_neutral_trade_does_not_touch_directional_exposure(fake_redis):
    t = _dir_trade(TradeStatus.OPEN)
    t.directional = False
    position_tracker.apply_trade(fake_redis, t, "default")
    assert load_state(fake_redis, "default").directional_exposure_pct == 0.0
