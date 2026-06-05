"""The owning user_id must flow from the Opportunity onto the simulated Trade."""

from __future__ import annotations

from datetime import datetime

import main
from models import SimulateRequest

from shared.models.opportunity import Opportunity, StrategyType


def _opp(user_id: str) -> Opportunity:
    return Opportunity(
        id="p1", user_id=user_id, strategy=StrategyType.CROSS_EXCHANGE, asset="BTC",
        long_exchange="kraken", short_exchange="crypto.com", gross_spread_bps=120.0,
        trading_fees_bps=33.5, slippage_estimate_bps=4.0, funding_rate_annualized_pct=0.0,
        net_edge_bps=70.0, confidence_score=0.8, recommended_size_usd=10_000.0,
        min_hold_hours=1.0, detected_at=datetime(2026, 1, 1), execute=True,
        long_reference_price=60_000.0, short_reference_price=60_010.0,
    )


def test_trade_inherits_opportunity_user_id() -> None:
    trade = main._simulate_internal(
        SimulateRequest(opportunity=_opp("user-42"), long_reference_price=60_000.0,
                        short_reference_price=60_010.0)
    )
    assert trade is not None
    assert trade.user_id == "user-42"


def test_default_user_id_when_unset() -> None:
    trade = main._simulate_internal(
        SimulateRequest(opportunity=_opp("default"), long_reference_price=60_000.0,
                        short_reference_price=60_010.0)
    )
    assert trade is not None and trade.user_id == "default"
