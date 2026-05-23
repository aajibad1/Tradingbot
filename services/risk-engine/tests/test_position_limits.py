from datetime import datetime

from rules.drawdown_guard import DEFAULT_LIMITS
from rules.position_limits import check_position_limits

from shared.models.opportunity import Opportunity, StrategyType
from shared.models.risk_state import KillSwitchState, RiskRule, RiskState


# Default net_edge_bps is comfortably above the 50-bp MIN_NET_EDGE bar so
# unrelated tests don't trip the min-edge rule. Tests that need to exercise
# the min-edge rule should pass an explicit low value.
_DEFAULT_NET_EDGE_BPS = 75.0


def _opp(size_usd: float = 1_000.0, net_edge_bps: float = _DEFAULT_NET_EDGE_BPS) -> Opportunity:
    return Opportunity(
        id="o-1",
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
    )


def _state(
    capital: float = 100_000.0,
    exchange_exposure: dict[str, float] | None = None,
    asset_concentration: dict[str, float] | None = None,
) -> RiskState:
    return RiskState(
        capital_usd=capital,
        daily_pnl_usd=0.0,
        daily_loss_pct=0.0,
        open_position_count=0,
        exchange_exposure_pct=exchange_exposure or {},
        asset_concentration_pct=asset_concentration or {},
        leverage_multiplier=1.0,
        kill_switch=KillSwitchState(active=False),
        last_updated=datetime.utcnow(),
    )


def test_approves_small_trade() -> None:
    violations = check_position_limits(_opp(size_usd=1_000), _state(), DEFAULT_LIMITS)
    assert violations == []


def test_rejects_trade_over_size_limit() -> None:
    # 3% of 100K = 3000, > 2% limit
    violations = check_position_limits(_opp(size_usd=3_000), _state(), DEFAULT_LIMITS)
    assert any(v.rule == RiskRule.PER_TRADE_SIZE_LIMIT for v in violations)


def test_rejects_exchange_exposure_breach_long_leg() -> None:
    # Kraken already at 24% → adding 2% pushes to 26% > 25% limit
    state = _state(exchange_exposure={"kraken": 24.0})
    violations = check_position_limits(_opp(size_usd=2_000), state, DEFAULT_LIMITS)
    assert any(v.rule == RiskRule.EXCHANGE_EXPOSURE_LIMIT for v in violations)


def test_rejects_exchange_exposure_breach_short_leg() -> None:
    """Concentration limit must apply to BOTH legs of the trade, not just the
    long. A perp venue already heavy on exposure should block new shorts."""
    state = _state(exchange_exposure={"hyperliquid": 24.0})
    violations = check_position_limits(_opp(size_usd=2_000), state, DEFAULT_LIMITS)
    assert any(
        v.rule == RiskRule.EXCHANGE_EXPOSURE_LIMIT and "hyperliquid" in v.message
        for v in violations
    )


def test_rejects_low_net_edge() -> None:
    # 5 bps net is below the 50-bp MIN_NET_EDGE bar after the gap-fix-critical bump.
    violations = check_position_limits(_opp(net_edge_bps=5.0), _state(), DEFAULT_LIMITS)
    assert any(v.rule == RiskRule.MIN_NET_EDGE for v in violations)


def test_rejects_marginal_net_edge() -> None:
    """An opportunity scoring 25 bps was viable under the old 8-bp regime but
    must be rejected under the new 50-bp profitability floor."""
    violations = check_position_limits(_opp(net_edge_bps=25.0), _state(), DEFAULT_LIMITS)
    assert any(v.rule == RiskRule.MIN_NET_EDGE for v in violations)
