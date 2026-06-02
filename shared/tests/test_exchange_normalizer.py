"""Tests for cross-venue symbol normalization."""

import pytest

from shared.utils.exchange_normalizer import base_asset, normalize_symbol


@pytest.mark.parametrize(
    "exchange,raw,instrument,expected",
    [
        ("coinbase", "BTC-USD", "spot", "BTC/USD"),
        ("kraken", "XBT/USD", "spot", "BTC/USD"),       # XBT → BTC
        ("kraken", "XDG/USD", "spot", "DOGE/USD"),      # XDG → DOGE
        ("binance.us", "ETH/USDT", "spot", "ETH/USD"),  # USDT → USD
        ("crypto.com", "SOL/USDC", "spot", "SOL/USD"),  # USDC → USD
        ("kraken", "BTC/ZUSD", "spot", "BTC/USD"),      # ZUSD → USD
        ("hyperliquid", "BTC", "perp", "BTC/USD:PERP"),  # bare base + perp suffix
        ("kraken", "XBT/USD", "perp", "BTC/USD:PERP"),
    ],
)
def test_normalize_symbol(exchange, raw, instrument, expected) -> None:
    assert normalize_symbol(exchange, raw, instrument) == expected


def test_normalize_symbol_raises_on_unparseable() -> None:
    with pytest.raises(ValueError):
        normalize_symbol("binance.us", "BTCUSD")  # no '/' or '-', not hyperliquid


def test_base_asset() -> None:
    assert base_asset("BTC/USD") == "BTC"
    assert base_asset("ETH/USD:PERP") == "ETH"
