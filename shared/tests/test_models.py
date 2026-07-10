"""Wire-contract tests for the shared Pydantic models.

These models are the cross-service contract carried over Pub/Sub — a field
rename or a tightened constraint breaks every consumer at once, so the
validation rules deserve explicit coverage.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from shared.models.exchange_tick import ExchangeTick, OrderBookLevel, OrderBookSnapshot
from shared.models.funding_rate import FundingRate
from shared.models.opportunity import Opportunity, StrategyType
from shared.models.risk_decision import RiskDecision
from shared.models.risk_state import (
    KillSwitchState,
    RiskRule,
    RiskState,
    RiskViolation,
)
from shared.models.trade import Trade, TradeLeg, TradeStatus, TradeType


def test_opportunity_roundtrips_json() -> None:
    opp = Opportunity(
        id="o1",
        strategy=StrategyType.CROSS_EXCHANGE,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="crypto.com",
        gross_spread_bps=120.0,
        trading_fees_bps=33.5,
        slippage_estimate_bps=4.0,
        net_edge_bps=79.5,
        confidence_score=0.8,
        recommended_size_usd=10_000.0,
        min_hold_hours=4.0,
        detected_at=datetime(2026, 1, 1),
    )
    assert opp.execute is False
    again = Opportunity.model_validate_json(opp.model_dump_json())
    assert again == opp
    assert again.strategy is StrategyType.CROSS_EXCHANGE


@pytest.mark.parametrize("bad", [{"confidence_score": 1.5}, {"recommended_size_usd": 0.0}, {"min_hold_hours": -1.0}])
def test_opportunity_field_constraints(bad: dict) -> None:
    base = dict(
        id="o1",
        strategy=StrategyType.CROSS_EXCHANGE,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="crypto.com",
        gross_spread_bps=1.0,
        trading_fees_bps=1.0,
        slippage_estimate_bps=1.0,
        net_edge_bps=1.0,
        confidence_score=0.5,
        recommended_size_usd=10_000.0,
        min_hold_hours=1.0,
        detected_at=datetime(2026, 1, 1),
    )
    base.update(bad)
    with pytest.raises(ValidationError):
        Opportunity(**base)


def test_trade_legs_and_defaults() -> None:
    leg = TradeLeg(
        exchange="kraken", side="buy", asset="BTC", size=0.1, fill_price=60_000.0,
        fee_usd=15.0, slippage_usd=1.0, filled_at=datetime(2026, 1, 1),
    )
    trade = Trade(
        id="t1", opportunity_id="o1", type=TradeType.PAPER, legs=[leg],
        gross_pnl_usd=10.0, net_pnl_usd=5.0, status=TradeStatus.CLOSED,
        opened_at=datetime(2026, 1, 1),
    )
    assert trade.funding_collected_usd == 0.0
    assert trade.closed_at is None
    assert trade.type is TradeType.PAPER
    assert Trade.model_validate_json(trade.model_dump_json()) == trade


def test_trade_leg_rejects_nonpositive_size_and_price() -> None:
    with pytest.raises(ValidationError):
        TradeLeg(exchange="k", side="buy", asset="BTC", size=0.0, fill_price=1.0,
                 fee_usd=0.0, slippage_usd=0.0, filled_at=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        TradeLeg(exchange="k", side="buy", asset="BTC", size=1.0, fill_price=0.0,
                 fee_usd=0.0, slippage_usd=0.0, filled_at=datetime(2026, 1, 1))


def test_funding_rate_period_must_be_positive() -> None:
    ok = FundingRate(
        exchange="hyperliquid", asset="BTC", symbol="BTC/USD:PERP",
        rate_per_period=0.0001, period_hours=1.0, annualized_pct=30.0,
        observed_at=datetime(2026, 1, 1), source="ccxt",
    )
    assert ok.next_funding_time is None
    with pytest.raises(ValidationError):
        FundingRate(
            exchange="kraken", asset="BTC", symbol="BTC/USD:PERP",
            rate_per_period=0.0001, period_hours=0.0, annualized_pct=1.0,
            observed_at=datetime(2026, 1, 1), source="ccxt",
        )


def test_exchange_tick_and_orderbook() -> None:
    tick = ExchangeTick(
        exchange="kraken", symbol="BTC/USD", asset="BTC", bid=60_000.0, ask=60_010.0,
        bid_size=1.0, ask_size=1.0, timestamp=datetime(2026, 1, 1),
    )
    assert tick.instrument_type == "spot"
    assert tick.last is None
    with pytest.raises(ValidationError):
        ExchangeTick(exchange="k", symbol="BTC/USD", asset="BTC", bid=0.0, ask=1.0,
                     bid_size=1.0, ask_size=1.0, timestamp=datetime(2026, 1, 1))

    snap = OrderBookSnapshot(
        exchange="kraken", symbol="BTC/USD",
        bids=[OrderBookLevel(price=60_000.0, size=2.0)],
        asks=[OrderBookLevel(price=60_010.0, size=2.0)],
        timestamp=datetime(2026, 1, 1),
    )
    assert snap.bids[0].price == 60_000.0


def test_risk_state_and_kill_switch_defaults() -> None:
    state = RiskState(capital_usd=100_000.0, last_updated=datetime(2026, 1, 1))
    assert state.leverage_multiplier == 1.0
    assert state.kill_switch.active is False
    assert state.exchange_exposure_pct == {}
    with pytest.raises(ValidationError):
        RiskState(capital_usd=0.0, last_updated=datetime(2026, 1, 1))

    v = RiskViolation(rule=RiskRule.DAILY_LOSS_LIMIT, observed=2.0, limit=1.0, message="x")
    assert v.rule.value == "daily_loss_limit"
    ks = KillSwitchState(active=True, triggered_by="risk-engine", reason="drawdown")
    assert ks.active and ks.triggered_by == "risk-engine"


def test_risk_decision_roundtrips_json() -> None:
    d = RiskDecision(
        decision_id="o1:rd", opportunity_id="o1", tenant_id="t1",
        strategy=StrategyType.FUNDING_RATE_ARB, asset="BTC",
        long_exchange="kraken", short_exchange="hyperliquid", directional=False,
        net_edge_bps=60.0, gross_spread_bps=0.0, trading_fees_bps=31.0,
        slippage_estimate_bps=4.0, funding_rate_annualized_pct=15.0,
        confidence_score=0.7, recommended_size_usd=10_000.0,
        approved=False, kill_switch_active=False,
        violation_rules=["per_trade_size_limit"], decided_at=datetime(2026, 1, 1),
    )
    back = RiskDecision.model_validate_json(d.model_dump_json())
    assert back == d
    assert back.violation_rules == ["per_trade_size_limit"]
    # confidence_score is bounded 0..1 — the wire constraint must hold.
    with pytest.raises(ValidationError):
        RiskDecision(**{**d.model_dump(), "confidence_score": 1.5})


def test_enum_value_stability() -> None:
    # These string values are the wire contract — guard against accidental rename.
    assert StrategyType.FUNDING_RATE_ARB.value == "funding_rate_arb"
    assert TradeType.LIVE.value == "live"
    assert TradeStatus.OPEN.value == "open"
    assert RiskRule.KILL_SWITCH.value == "kill_switch"


def test_movement_signal_journal_fact():
    from datetime import datetime
    import pytest
    from pydantic import ValidationError
    from shared.models.movement_signal import MovementSignal

    sig = MovementSignal(signal_id="s1", symbol="BTC/USD:PERP",
                         family="momentum_dislocation", direction="long",
                         gross_edge_bps=65.0, confidence=0.9, expiry_ms=2000,
                         detected_at=datetime(2026, 1, 1))
    assert sig.regime is None  # ungated detection is a valid fact
    with pytest.raises(ValidationError):
        MovementSignal(signal_id="s2", symbol="X", family="f", direction="long",
                       gross_edge_bps=1.0, confidence=1.5, expiry_ms=0,  # confidence > 1
                       detected_at=datetime(2026, 1, 1))
