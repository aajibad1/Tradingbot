"""Integration tests for end-to-end paper-trader flow."""

from datetime import datetime

from shared.models.opportunity import Opportunity, StrategyType
from models import SimulateRequest


def test_full_simulation_flow() -> None:
    """Test complete simulation from opportunity to trade record."""
    from main import _simulate_internal

    # Create a valid approved opportunity
    opp = Opportunity(
        id="test-opp-123",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="coinbase",
        short_exchange="hyperliquid",
        gross_spread_bps=25.0,
        trading_fees_bps=65.0,  # coinbase 60 + hyperliquid 5
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=12.0,
        net_edge_bps=10.0,
        confidence_score=0.85,
        recommended_size_usd=25_000.0,
        min_hold_hours=8.0,
        detected_at=datetime(2026, 1, 1, 12, 0),
        execute=True,  # CRITICAL: approved by risk-engine
    )

    req = SimulateRequest(
        opportunity=opp,
        long_reference_price=60_000.0,
        short_reference_price=60_015.0,
        long_book_depth_usd=500_000.0,
        short_book_depth_usd=1_000_000.0,
    )

    trade = _simulate_internal(req)

    assert trade is not None
    assert trade.opportunity_id == "test-opp-123"
    assert trade.type.value == "paper"
    assert trade.status.value == "closed"
    assert len(trade.legs) == 2

    # Long leg (buy on coinbase)
    long_leg = trade.legs[0]
    assert long_leg.exchange == "coinbase"
    assert long_leg.side == "buy"
    assert long_leg.asset == "BTC"
    assert long_leg.fee_usd > 0
    assert long_leg.slippage_usd > 0

    # Short leg (sell on hyperliquid)
    short_leg = trade.legs[1]
    assert short_leg.exchange == "hyperliquid"
    assert short_leg.side == "sell"
    assert short_leg.asset == "BTC"
    assert short_leg.fee_usd > 0

    # PnL components
    assert trade.gross_pnl_usd > 0  # Spread capture
    assert trade.funding_collected_usd > 0  # Positive funding on short perp
    # Net PnL = gross + funding - fees - slippage
    expected_net = (
        trade.gross_pnl_usd
        + trade.funding_collected_usd
        - sum(leg.fee_usd for leg in trade.legs)
        - sum(leg.slippage_usd for leg in trade.legs)
    )
    assert abs(trade.net_pnl_usd - expected_net) < 0.01

    # Timing
    assert trade.opened_at is not None
    assert trade.closed_at is not None
    assert trade.closed_at > trade.opened_at


def test_rejected_opportunity_not_simulated() -> None:
    """Opportunities with execute=False should be rejected."""
    from main import _simulate_internal

    opp = Opportunity(
        id="test-opp-rejected",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="coinbase",
        short_exchange="hyperliquid",
        gross_spread_bps=25.0,
        trading_fees_bps=65.0,
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=12.0,
        net_edge_bps=10.0,
        confidence_score=0.85,
        recommended_size_usd=25_000.0,
        min_hold_hours=8.0,
        detected_at=datetime(2026, 1, 1, 12, 0),
        execute=False,  # NOT approved
    )

    req = SimulateRequest(
        opportunity=opp,
        long_reference_price=60_000.0,
        short_reference_price=60_015.0,
    )

    trade = _simulate_internal(req)
    assert trade is None
