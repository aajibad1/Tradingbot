"""Normalize per-exchange symbols to a common form, e.g. 'BTC/USD'.

Each exchange has its own quirks: Coinbase uses 'BTC-USD', Kraken uses 'XBT/USD',
Hyperliquid uses 'BTC' with implicit USDC perp quote, etc. We standardize on
'<BASE>/<QUOTE>' (spot) or '<BASE>/<QUOTE>:PERP' for perpetuals.
"""

_KRAKEN_BASE_REMAP = {"XBT": "BTC", "XDG": "DOGE"}


def normalize_symbol(exchange: str, raw_symbol: str, instrument_type: str = "spot") -> str:
    """Return canonical symbol. Raises ValueError on inputs we can't parse."""
    exchange_l = exchange.lower()
    if "/" in raw_symbol:
        base, quote = raw_symbol.split("/", 1)
    elif "-" in raw_symbol:
        base, quote = raw_symbol.split("-", 1)
    elif exchange_l == "hyperliquid":
        # Hyperliquid perps are quoted in USDC and use the bare base symbol.
        base, quote = raw_symbol, "USDC"
    else:
        raise ValueError(f"cannot parse symbol {raw_symbol!r} on {exchange!r}")

    base = _KRAKEN_BASE_REMAP.get(base.upper(), base.upper())
    quote = quote.upper()
    # USD/USDT/USDC are all collapsed to USD for cross-venue comparisons.
    if quote in {"USDT", "USDC", "ZUSD"}:
        quote = "USD"

    canonical = f"{base}/{quote}"
    if instrument_type == "perp":
        canonical += ":PERP"
    return canonical


def base_asset(canonical_symbol: str) -> str:
    return canonical_symbol.split("/", 1)[0]
