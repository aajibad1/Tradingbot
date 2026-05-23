from datetime import datetime

from hummingbot_client import (
    ARB_LEVERAGE,
    HummingbotCloseRequest,
    HummingbotOrderRequest,
    ISOLATED_MARGIN_MODE,
    perp_risk_params,
)

from shared.models.opportunity import Opportunity, StrategyType


def _opportunity(execute: bool = True) -> Opportunity:
    return Opportunity(
        id="opp-123",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="hyperliquid",
        gross_spread_bps=20.0,
        trading_fees_bps=31.0,
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=15.0,
        net_edge_bps=12.0,
        confidence_score=0.85,
        recommended_size_usd=10_000.0,
        min_hold_hours=6.0,
        detected_at=datetime(2026, 1, 1),
        execute=execute,
    )


def test_order_request_from_opportunity_preserves_all_fields() -> None:
    req = HummingbotOrderRequest.from_opportunity("bot-1", _opportunity())
    assert req.bot_id == "bot-1"
    assert req.asset == "BTC"
    assert req.long_exchange == "kraken"
    assert req.short_exchange == "hyperliquid"
    assert req.size_usd == 10_000.0
    assert req.opportunity_id == "opp-123"
    assert req.strategy == "funding_rate_arb"


def test_order_request_payload_round_trip() -> None:
    payload = HummingbotOrderRequest.from_opportunity("bot-1", _opportunity()).to_payload()
    assert payload["asset"] == "BTC"
    assert payload["strategy"] == "funding_rate_arb"
    assert payload["opportunity_id"] == "opp-123"
    assert payload["metadata"]["confidence_score"] == 0.85


def test_open_order_uses_isolated_margin_and_1x_leverage() -> None:
    """Margin policy: every open order is isolated + 1x leverage + not reduceOnly."""
    payload = HummingbotOrderRequest.from_opportunity("bot-1", _opportunity()).to_payload()
    assert payload["risk_params"]["marginMode"] == ISOLATED_MARGIN_MODE == "isolated"
    assert payload["risk_params"]["leverage"] == ARB_LEVERAGE == 1
    assert payload["risk_params"]["reduceOnly"] is False


def test_close_request_is_reduce_only() -> None:
    """Close requests must carry reduceOnly=True so a misroute can't open exposure."""
    close = HummingbotCloseRequest(bot_id="bot-1", opportunity_id="opp-123")
    payload = close.to_payload()
    assert payload["opportunity_id"] == "opp-123"
    assert payload["risk_params"]["marginMode"] == "isolated"
    assert payload["risk_params"]["leverage"] == 1
    assert payload["risk_params"]["reduceOnly"] is True


def test_perp_risk_params_default_is_open_position() -> None:
    p = perp_risk_params()
    assert p == {"marginMode": "isolated", "leverage": 1, "reduceOnly": False}
    p_close = perp_risk_params(reduce_only=True)
    assert p_close["reduceOnly"] is True
