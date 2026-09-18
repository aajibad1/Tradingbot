from __future__ import annotations

from datetime import datetime, timedelta

from journal import SignalLedger
from shared.models.movement_signal import MovementSignal
from shared.models.trade import Trade, TradeLeg, TradeStatus, TradeType


def _signal(signal_id="sig-1", expiry_ms=2000, detected_at=None, **overrides):
    base = dict(
        signal_id=signal_id, symbol="BTC/USD:PERP", family="momentum_dislocation",
        direction="long", gross_edge_bps=30.0, confidence=0.8, expiry_ms=expiry_ms,
        regime="trending", detected_at=detected_at or datetime(2026, 1, 1, 12, 0, 0),
    )
    base.update(overrides)
    return MovementSignal(**base)


def _leg(size=1.0, fill_price=60_000.0):
    return TradeLeg(exchange="hyperliquid", side="buy", asset="BTC", size=size,
                     fill_price=fill_price, fee_usd=1.0, slippage_usd=1.0,
                     filled_at=datetime(2026, 1, 1, 13, 0, 0))


def _trade(opportunity_id="sig-1", net_pnl_usd=120.0, legs=None):
    legs = legs if legs is not None else [_leg(), _leg()]
    return Trade(id="trade-1", opportunity_id=opportunity_id, type=TradeType.PAPER,
                 legs=legs, gross_pnl_usd=150.0, net_pnl_usd=net_pnl_usd,
                 status=TradeStatus.CLOSED, opened_at=datetime(2026, 1, 1, 12, 30, 0),
                 closed_at=datetime(2026, 1, 1, 13, 0, 0), directional=True)


def test_record_fill_joins_and_scores_true_positive():
    ledger = SignalLedger()
    ledger.record_signal(_signal())
    scored = ledger.record_fill(_trade(net_pnl_usd=120.0))

    assert scored is not None
    assert scored.signal_id == "sig-1"
    assert scored.label == "true_positive"
    assert scored.outcome.realized_bps > 0
    assert ledger.pending_count == 0
    assert len(ledger.scored) == 1


def test_record_fill_false_positive_on_loss():
    ledger = SignalLedger()
    ledger.record_signal(_signal())
    scored = ledger.record_fill(_trade(net_pnl_usd=-60.0))

    assert scored is not None
    assert scored.label == "false_positive"
    assert scored.outcome.realized_bps < 0


def test_record_fill_miss_returns_none_and_does_not_score():
    ledger = SignalLedger()
    ledger.record_signal(_signal(signal_id="sig-1"))
    scored = ledger.record_fill(_trade(opportunity_id="unrelated-opp"))

    assert scored is None
    assert ledger.pending_count == 1
    assert ledger.scored == []


def test_record_fill_zero_notional_scores_zero_bps_not_crash():
    ledger = SignalLedger()
    ledger.record_signal(_signal())
    scored = ledger.record_fill(_trade(legs=[]))

    assert scored is not None
    assert scored.outcome.realized_bps == 0.0


def test_sweep_expired_labels_decayed_and_clears_pending():
    ledger = SignalLedger()
    ledger.record_signal(_signal(expiry_ms=1000, detected_at=datetime(2026, 1, 1, 12, 0, 0)))

    swept = ledger.sweep_expired(now=datetime(2026, 1, 1, 12, 0, 2))

    assert len(swept) == 1
    assert swept[0].label == "decayed"
    assert swept[0].outcome.reached_execution is False
    assert ledger.pending_count == 0
    assert len(ledger.scored) == 1


def test_sweep_expired_leaves_unexpired_pending():
    ledger = SignalLedger()
    ledger.record_signal(_signal(expiry_ms=10_000, detected_at=datetime(2026, 1, 1, 12, 0, 0)))

    swept = ledger.sweep_expired(now=datetime(2026, 1, 1, 12, 0, 2))

    assert swept == []
    assert ledger.pending_count == 1


def test_sweep_expired_skips_zero_expiry_signals():
    ledger = SignalLedger()
    ledger.record_signal(_signal(expiry_ms=0))

    swept = ledger.sweep_expired(now=datetime(2026, 1, 1, 12, 0, 0) + timedelta(days=1))

    assert swept == []
    assert ledger.pending_count == 1


def test_record_fill_after_sweep_no_longer_matches():
    ledger = SignalLedger()
    ledger.record_signal(_signal(expiry_ms=1000, detected_at=datetime(2026, 1, 1, 12, 0, 0)))
    ledger.sweep_expired(now=datetime(2026, 1, 1, 12, 0, 2))

    scored = ledger.record_fill(_trade())

    assert scored is None
    assert len(ledger.scored) == 1  # only the decayed entry
