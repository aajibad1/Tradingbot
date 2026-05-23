from collectors.base import BaseCollector, CollectorConfig


class _Stub(BaseCollector):
    name = "stub"

    async def _build_client(self):  # noqa: D401
        raise NotImplementedError


def test_normalizes_kraken_xbt_ticker() -> None:
    c = _Stub(CollectorConfig(exchange="kraken", symbols=["XBT/USD"], instrument_type="spot"))
    tick = c._to_exchange_tick(
        "XBT/USD",
        {
            "bid": 60_000.0,
            "ask": 60_010.0,
            "bidVolume": 1.2,
            "askVolume": 0.8,
            "last": 60_005.0,
            "timestamp": 1_700_000_000_000,
        },
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD"
    assert tick.asset == "BTC"
    assert tick.exchange == "kraken"
    assert tick.bid == 60_000.0
    assert tick.ask == 60_010.0


def test_rejects_zero_bid_ask() -> None:
    c = _Stub(CollectorConfig(exchange="coinbase", symbols=["BTC-USD"]))
    assert c._to_exchange_tick("BTC-USD", {"bid": 0, "ask": 60_000}) is None
    assert c._to_exchange_tick("BTC-USD", {"bid": None, "ask": None}) is None


def test_hyperliquid_bare_perp_symbol() -> None:
    c = _Stub(CollectorConfig(exchange="hyperliquid", symbols=["BTC"], instrument_type="perp"))
    tick = c._to_exchange_tick(
        "BTC",
        {"bid": 60_000.0, "ask": 60_001.0, "timestamp": 1_700_000_000_000},
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD:PERP"
    assert tick.instrument_type == "perp"
