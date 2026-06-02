"""C1 regression: mutable risk state must actually be populated.

Before this fix, risk:daily_pnl_usd / risk:open_positions / risk:exposure:* /
risk:concentration:* were only ever read — nothing wrote them, so the drawdown
guard and exposure/concentration limits could never fire. These tests prove the
keys move in response to trade fills and that the drawdown guard trips on a real
realized loss.
"""

from __future__ import annotations

from datetime import datetime

import position_tracker
from rules.drawdown_guard import DEFAULT_LIMITS, check_drawdown
from state import (
    KEY_DAILY_PNL,
    KEY_OPEN_POSITIONS,
    PREFIX_CONCENTRATION,
    PREFIX_EXPOSURE,
    load_state,
)

from shared.models.trade import Trade, TradeLeg, TradeStatus, TradeType


def _leg(exchange: str, side: str, size: float, price: float) -> TradeLeg:
    return TradeLeg(
        exchange=exchange,
        side=side,
        asset="BTC",
        size=size,
        fill_price=price,
        fee_usd=5.0,
        slippage_usd=2.0,
        filled_at=datetime(2026, 1, 1),
    )


def _trade(opp_id: str, status: TradeStatus, net_pnl: float, legs: list[TradeLeg]) -> Trade:
    return Trade(
        id=f"trade-{opp_id}",
        opportunity_id=opp_id,
        type=TradeType.PAPER,
        legs=legs,
        gross_pnl_usd=net_pnl,
        net_pnl_usd=net_pnl,
        status=status,
        opened_at=datetime(2026, 1, 1),
        closed_at=datetime(2026, 1, 1) if status is TradeStatus.CLOSED else None,
    )


def test_closed_trade_realizes_daily_pnl(fake_redis) -> None:
    """A closed losing paper trade must move risk:daily_pnl_usd (capital=100k)."""
    legs = [_leg("kraken", "buy", 0.1, 60_000.0), _leg("hyperliquid", "sell", 0.1, 60_000.0)]
    position_tracker.apply_trade(fake_redis, _trade("opp-1", TradeStatus.CLOSED, -250.0, legs))
    assert float(fake_redis.get(KEY_DAILY_PNL)) == -250.0
    # And it accumulates across fills.
    position_tracker.apply_trade(fake_redis, _trade("opp-2", TradeStatus.CLOSED, -300.0, legs))
    assert float(fake_redis.get(KEY_DAILY_PNL)) == -550.0


def test_drawdown_guard_actually_fires_after_loss(fake_redis) -> None:
    """The whole point of C1: a realized loss past the daily limit trips the guard.

    capital=100k, max_daily_loss_pct=1.0 → a -1,500 USD day (1.5%) must violate.
    """
    legs = [_leg("kraken", "buy", 0.1, 60_000.0), _leg("hyperliquid", "sell", 0.1, 60_000.0)]
    position_tracker.apply_trade(fake_redis, _trade("opp-loss", TradeStatus.CLOSED, -1_500.0, legs))

    state = load_state(fake_redis)
    assert state.daily_pnl_usd == -1_500.0
    assert abs(state.daily_loss_pct - 1.5) < 1e-9
    violation = check_drawdown(state, DEFAULT_LIMITS)
    assert violation is not None  # before C1 this was always None (dead check)


def test_open_then_close_rolls_exposure_in_and_out(fake_redis) -> None:
    # 0.1 BTC @ 60k = $6,000 per leg = 6% of $100k capital.
    legs = [_leg("kraken", "buy", 0.1, 60_000.0), _leg("hyperliquid", "sell", 0.1, 60_000.0)]
    position_tracker.apply_trade(fake_redis, _trade("opp-x", TradeStatus.OPEN, 0.0, legs))

    state = load_state(fake_redis)
    assert state.open_position_count == 1
    assert abs(state.exchange_exposure_pct["kraken"] - 6.0) < 1e-9
    assert abs(state.exchange_exposure_pct["hyperliquid"] - 6.0) < 1e-9
    assert abs(state.asset_concentration_pct["BTC"] - 6.0) < 1e-9

    # Closing the same opportunity releases the exposure back to ~0.
    position_tracker.apply_trade(fake_redis, _trade("opp-x", TradeStatus.CLOSED, 120.0, legs))
    state = load_state(fake_redis)
    assert state.open_position_count == 0
    assert state.exchange_exposure_pct == {}
    assert state.asset_concentration_pct == {}
    assert float(fake_redis.get(KEY_DAILY_PNL)) == 120.0


def test_open_is_idempotent_on_redelivery(fake_redis) -> None:
    """At-least-once Pub/Sub delivery must not double-count exposure."""
    legs = [_leg("kraken", "buy", 0.1, 60_000.0), _leg("hyperliquid", "sell", 0.1, 60_000.0)]
    position_tracker.apply_trade(fake_redis, _trade("opp-dup", TradeStatus.OPEN, 0.0, legs))
    position_tracker.apply_trade(fake_redis, _trade("opp-dup", TradeStatus.OPEN, 0.0, legs))
    assert int(fake_redis.get(KEY_OPEN_POSITIONS)) == 1
    assert abs(float(fake_redis.get(f"{PREFIX_EXPOSURE}kraken")) - 6.0) < 1e-9
    assert abs(float(fake_redis.get(f"{PREFIX_CONCENTRATION}BTC")) - 6.0) < 1e-9
