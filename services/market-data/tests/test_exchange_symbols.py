"""Test symbol normalization for each exchange's specific quirks."""

from collectors.base import BaseCollector, CollectorConfig


class _Stub(BaseCollector):
    name = "stub"

    async def _build_client(self):
        raise NotImplementedError


def test_coinbase_normalizes_dash_separator():
    """Coinbase uses 'BTC-USD' format."""
    c = _Stub(CollectorConfig(exchange="coinbase", symbols=["BTC-USD"]))
    tick = c._to_exchange_tick(
        "BTC-USD",
        {"bid": 60000.0, "ask": 60010.0, "last": 60005.0, "timestamp": 1700000000000},
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD"
    assert tick.asset == "BTC"


def test_coinbase_usdc_normalizes_to_usd():
    """Coinbase USDC pairs should normalize to USD."""
    c = _Stub(CollectorConfig(exchange="coinbase", symbols=["BTC-USDC"]))
    tick = c._to_exchange_tick(
        "BTC-USDC",
        {"bid": 60000.0, "ask": 60001.0, "timestamp": 1700000000000},
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD"


def test_kraken_xbt_to_btc():
    """Kraken uses XBT instead of BTC."""
    c = _Stub(CollectorConfig(exchange="kraken", symbols=["XBT/USD"]))
    tick = c._to_exchange_tick(
        "XBT/USD",
        {"bid": 60000.0, "ask": 60010.0, "timestamp": 1700000000000},
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD"
    assert tick.asset == "BTC"


def test_kraken_zusd_to_usd():
    """Kraken uses ZUSD for USD."""
    c = _Stub(CollectorConfig(exchange="kraken", symbols=["XBT/ZUSD"]))
    tick = c._to_exchange_tick(
        "XBT/ZUSD",
        {"bid": 60000.0, "ask": 60010.0, "timestamp": 1700000000000},
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD"


def test_crypto_com_spot_normalized():
    """Crypto.com symbols are already normalized by CCXT to use '/'."""
    c = _Stub(CollectorConfig(exchange="crypto.com", symbols=["BTC/USDT"]))
    tick = c._to_exchange_tick(
        "BTC/USDT",
        {"bid": 60000.0, "ask": 60001.0, "timestamp": 1700000000000},
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD"  # USDT normalizes to USD


def test_crypto_com_perp():
    """Crypto.com perps - CCXT typically returns these as BTC/USDT for the contract."""
    c = _Stub(
        CollectorConfig(exchange="crypto.com", symbols=["BTC/USDT"], instrument_type="perp")
    )
    tick = c._to_exchange_tick(
        "BTC/USDT",
        {"bid": 60000.0, "ask": 60001.0, "timestamp": 1700000000000},
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD:PERP"
    assert tick.instrument_type == "perp"


def test_binance_us_spot_normalized():
    """Binance.US symbols normalized by CCXT to use '/'."""
    c = _Stub(CollectorConfig(exchange="binance.us", symbols=["BTC/USD"]))
    tick = c._to_exchange_tick(
        "BTC/USD",
        {"bid": 60000.0, "ask": 60001.0, "timestamp": 1700000000000},
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD"


def test_hyperliquid_perp_bare_symbol():
    """Hyperliquid uses bare base asset symbol for perps."""
    c = _Stub(CollectorConfig(exchange="hyperliquid", symbols=["BTC"], instrument_type="perp"))
    tick = c._to_exchange_tick(
        "BTC",
        {"bid": 60000.0, "ask": 60001.0, "timestamp": 1700000000000},
    )
    assert tick is not None
    assert tick.symbol == "BTC/USD:PERP"
    assert tick.instrument_type == "perp"
    assert tick.asset == "BTC"


def test_hyperliquid_eth_perp():
    """Hyperliquid ETH perp."""
    c = _Stub(CollectorConfig(exchange="hyperliquid", symbols=["ETH"], instrument_type="perp"))
    tick = c._to_exchange_tick(
        "ETH",
        {"bid": 3000.0, "ask": 3001.0, "timestamp": 1700000000000},
    )
    assert tick is not None
    assert tick.symbol == "ETH/USD:PERP"
    assert tick.asset == "ETH"
