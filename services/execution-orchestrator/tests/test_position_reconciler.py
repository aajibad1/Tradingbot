from position_reconciler import (
    PositionRow,
    hummingbot_positions_from_api,
    reconcile,
)


def _pos(exchange: str, asset: str, size: float, price: float) -> PositionRow:
    return PositionRow(exchange=exchange, asset=asset, size=size, notional_usd=size * price)


def test_perfect_match_emits_nothing() -> None:
    hum = [_pos("kraken", "BTC", 0.5, 60_000)]
    exc = [_pos("kraken", "BTC", 0.5, 60_000)]
    assert reconcile(hum, exc) == []


def test_small_diff_is_ok_severity() -> None:
    # $30 diff on $30K notional → ok
    hum = [_pos("kraken", "BTC", 0.5, 60_000)]
    exc = [_pos("kraken", "BTC", 0.5005, 60_000)]
    [d] = reconcile(hum, exc)
    assert d.severity == "ok"


def test_large_diff_warns() -> None:
    hum = [_pos("kraken", "BTC", 0.5, 60_000)]
    exc = [_pos("kraken", "BTC", 0.55, 60_000)]  # $3000 diff
    [d] = reconcile(hum, exc)
    assert d.severity == "warn"


def test_missing_on_exchange_is_warn() -> None:
    hum = [_pos("kraken", "BTC", 0.5, 60_000)]
    exc: list[PositionRow] = []
    [d] = reconcile(hum, exc)
    assert d.severity == "warn"
    assert d.exchange_size == 0.0


def test_persistent_streak_escalates_to_critical() -> None:
    hum = [_pos("kraken", "BTC", 0.5, 60_000)]
    exc = [_pos("kraken", "BTC", 0.55, 60_000)]
    streak: dict = {}
    [d1] = reconcile(hum, exc, persistent_streak=streak)
    [d2] = reconcile(hum, exc, persistent_streak=streak)
    [d3] = reconcile(hum, exc, persistent_streak=streak)
    assert d1.severity == "warn"
    assert d2.severity == "warn"
    assert d3.severity == "critical"


def test_streak_resets_on_match() -> None:
    streak: dict = {}
    hum = [_pos("kraken", "BTC", 0.5, 60_000)]
    bad = [_pos("kraken", "BTC", 0.55, 60_000)]
    good = [_pos("kraken", "BTC", 0.5, 60_000)]
    reconcile(hum, bad, persistent_streak=streak)
    reconcile(hum, bad, persistent_streak=streak)
    assert streak[("kraken", "BTC")] == 2
    reconcile(hum, good, persistent_streak=streak)
    assert ("kraken", "BTC") not in streak


def test_adapter_handles_malformed_rows() -> None:
    rows = hummingbot_positions_from_api(
        [
            {"exchange": "kraken", "asset": "BTC", "size": "0.5", "mark_price": "60000"},
            {"exchange": "kraken"},  # malformed, should be skipped
            {"exchange": "hyperliquid", "asset": "ETH", "size": "0.0", "mark_price": "3500"},
        ]
    )
    assert len(rows) == 2
    assert rows[0].notional_usd == 30_000.0
