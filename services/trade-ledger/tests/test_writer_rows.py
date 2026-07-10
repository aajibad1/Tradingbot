from datetime import datetime

from writer import audit_log_to_row, funding_to_row, opportunity_to_row, risk_alert_to_row, signal_to_row, trade_to_row

from shared.models.funding_rate import FundingRate
from shared.models.movement_signal import MovementSignal
from shared.models.opportunity import Opportunity, StrategyType
from shared.models.trade import Trade, TradeLeg, TradeStatus, TradeType


def test_trade_to_row_flattens_legs_and_totals_fees() -> None:
    trade = Trade(
        id="t1",
        opportunity_id="o1",
        type=TradeType.PAPER,
        status=TradeStatus.CLOSED,
        gross_pnl_usd=10.0,
        net_pnl_usd=5.0,
        opened_at=datetime(2026, 1, 1, 12),
        closed_at=datetime(2026, 1, 1, 16),
        funding_collected_usd=1.5,
        legs=[
            TradeLeg(
                exchange="kraken",
                side="buy",
                asset="BTC",
                size=0.1,
                fill_price=60_000,
                fee_usd=15.6,
                slippage_usd=1.2,
                filled_at=datetime(2026, 1, 1, 12),
            ),
            TradeLeg(
                exchange="hyperliquid",
                side="sell",
                asset="BTC",
                size=0.1,
                fill_price=60_500,
                fee_usd=3.0,
                slippage_usd=1.0,
                filled_at=datetime(2026, 1, 1, 12),
            ),
        ],
    )
    row = trade_to_row(trade)
    assert row["trade_id"] == "t1"
    assert row["asset"] == "BTC"
    assert row["long_exchange"] == "kraken"
    assert row["short_exchange"] == "hyperliquid"
    assert row["total_fees_usd"] == 18.6
    assert row["total_slippage_usd"] == 2.2
    assert row["funding_collected_usd"] == 1.5
    assert len(row["legs"]) == 2
    assert row["legs"][0]["exchange"] == "kraken"


def test_opportunity_to_row_preserves_strategy_enum() -> None:
    opp = Opportunity(
        id="o1",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="hyperliquid",
        gross_spread_bps=20,
        trading_fees_bps=31,
        slippage_estimate_bps=4,
        funding_rate_annualized_pct=15.0,
        net_edge_bps=12.0,
        confidence_score=0.7,
        recommended_size_usd=10_000,
        min_hold_hours=6.0,
        detected_at=datetime(2026, 1, 1),
        execute=False,
    )
    row = opportunity_to_row(opp)
    assert row["strategy"] == "funding_rate_arb"
    assert row["net_edge_bps"] == 12.0
    assert row["execute"] is False


def test_funding_to_row_includes_source() -> None:
    rate = FundingRate(
        exchange="hyperliquid",
        asset="BTC",
        symbol="BTC/USD:PERP",
        rate_per_period=0.000034,
        period_hours=1.0,
        annualized_pct=30.0,
        observed_at=datetime(2026, 1, 1),
        source="ccxt",
    )
    row = funding_to_row(rate)
    assert row["source"] == "ccxt"
    assert row["period_hours"] == 1.0
    assert row["annualized_pct"] == 30.0


def test_risk_alert_to_row_uses_limit_value() -> None:
    """Verify risk_alert_to_row maps 'limit' payload field to 'limit_value' column."""
    payload = {
        "alert_type": "drawdown_breach",
        "severity": "critical",
        "message": "Daily drawdown exceeded limit",
        "rule": "max_daily_drawdown_pct",
        "observed": 12.5,
        "limit": 10.0,
        "source": "risk-engine",
        "emitted_at": "2026-01-01T12:00:00Z",
    }
    row = risk_alert_to_row(payload)
    assert row["alert_type"] == "drawdown_breach"
    assert row["severity"] == "critical"
    assert row["limit_value"] == 10.0  # mapped from payload["limit"]
    assert row["observed"] == 12.5
    assert "limit" not in row  # should not have the old key


def test_audit_log_to_row_includes_metadata() -> None:
    """Verify audit_log_to_row produces valid row dicts."""
    payload = {
        "event_id": "evt_123",
        "source": "ai-ops-agent",
        "event_type": "limit_change_proposed",
        "actor": "claude@ai-ops",
        "action": "propose_limit_change",
        "resource_type": "risk_limit",
        "resource_id": "max_position_size_usd",
        "metadata": {"old_value": 50000, "new_value": 75000},
        "emitted_at": "2026-01-01T14:00:00Z",
    }
    row = audit_log_to_row(payload)
    assert row["event_id"] == "evt_123"
    assert row["source"] == "ai-ops-agent"
    assert row["event_type"] == "limit_change_proposed"
    assert row["actor"] == "claude@ai-ops"
    assert row["metadata"] == {"old_value": 50000, "new_value": 75000}


def test_write_is_local_sink_without_project(monkeypatch, caplog) -> None:
    """In local / emulator mode (no GCP_PROJECT_ID) writes log instead of hitting
    BigQuery — so trade-ledger can run in the local stack without a BQ backend."""
    import logging

    import writer

    monkeypatch.setattr(writer, "_PROJECT", None)
    # _client() must NOT be called when there is no project.
    monkeypatch.setattr(writer, "_client",
                        lambda: (_ for _ in ()).throw(AssertionError("BQ client used in local mode")))
    trade = Trade(
        id="t-local", opportunity_id="o1", type=TradeType.PAPER, status=TradeStatus.CLOSED,
        gross_pnl_usd=1.0, net_pnl_usd=1.0, opened_at=datetime(2026, 1, 1),
        legs=[TradeLeg(exchange="kraken", side="buy", asset="BTC", size=0.1,
                       fill_price=60_000, fee_usd=1.0, slippage_usd=0.1,
                       filled_at=datetime(2026, 1, 1))],
    )
    with caplog.at_level(logging.INFO):
        writer.write_trade(trade)   # must not raise
    assert any("local-sink" in r.message for r in caplog.records)


def test_signal_to_row_serializes_detection_fact() -> None:
    row = signal_to_row(MovementSignal(
        signal_id="s1", symbol="BTC/USD:PERP", family="orderbook_imbalance",
        direction="short", gross_edge_bps=9.6, confidence=0.8, expiry_ms=800,
        regime=None, detected_at=datetime(2026, 1, 1, 12, 30),
    ))
    assert row["signal_id"] == "s1" and row["family"] == "orderbook_imbalance"
    assert row["regime"] is None                      # ungated detection journals as null
    assert row["detected_at"] == "2026-01-01T12:30:00"
