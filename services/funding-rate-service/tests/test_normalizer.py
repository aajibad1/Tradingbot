from normalizer import annualize, from_arbitrage_scanner, from_ccxt, from_coinglass


def test_annualize_8h_period() -> None:
    # 0.01% per 8h period = 0.01 * (8760 / 8) * 100 / 100 = 10.95% annualized
    apr = annualize(0.0001, 8.0)
    assert abs(apr - 10.95) < 0.01


def test_annualize_1h_period() -> None:
    # Hyperliquid: same rate, 8x the periods → 8x the APR
    apr = annualize(0.0001, 1.0)
    assert abs(apr - 87.6) < 0.1


def test_coinglass_normalizer() -> None:
    rate = from_coinglass({
        "symbol": "BTC",
        "exchangeName": "Kraken",
        "rate": 0.0001,
        "intervalHours": 8,
        "nextFundingTime": 1_700_000_000_000,
    })
    assert rate is not None
    assert rate.exchange == "kraken"
    assert rate.asset == "BTC"
    assert rate.symbol == "BTC/USD:PERP"
    assert rate.period_hours == 8.0
    assert rate.source == "coinglass"


def test_coinglass_rejects_malformed() -> None:
    assert from_coinglass({"symbol": "BTC"}) is None  # missing rate
    assert from_coinglass({"rate": "not-a-number", "symbol": "BTC", "exchangeName": "X"}) is None


def test_arb_scanner_normalizer() -> None:
    rate = from_arbitrage_scanner({
        "exchange": "Hyperliquid",
        "base": "ETH",
        "rate_8h": 0.0002,
    })
    assert rate is not None
    assert rate.exchange == "hyperliquid"
    assert rate.asset == "ETH"
    assert rate.period_hours == 8.0  # arb-scanner reports as 8h regardless


def test_ccxt_hyperliquid_uses_1h_period() -> None:
    rate = from_ccxt("hyperliquid", {"symbol": "BTC", "fundingRate": 0.00005})
    assert rate is not None
    assert rate.period_hours == 1.0
    assert rate.exchange == "hyperliquid"


def test_ccxt_cex_uses_8h_period() -> None:
    rate = from_ccxt("kraken", {"symbol": "XBT/USD", "fundingRate": 0.0001})
    assert rate is not None
    assert rate.period_hours == 8.0
    assert rate.symbol == "BTC/USD:PERP"
