"""Simulate funding payments on the perp leg of a position.

CEX perps (Kraken, Crypto.com, Binance) pay funding every 8 hours.
Hyperliquid pays every 1 hour.
"""

_PERIOD_HOURS_BY_EXCHANGE: dict[str, float] = {
    "kraken": 8.0,
    "crypto.com": 8.0,
    "binance.us": 8.0,
    "hyperliquid": 1.0,
}


def funding_period_hours(exchange: str) -> float:
    return _PERIOD_HOURS_BY_EXCHANGE.get(exchange.lower(), 8.0)


def simulate_funding_payment(
    notional_usd: float,
    funding_rate_pct_per_period: float,
    is_short_perp: bool,
    periods: int,
) -> float:
    """Funding USD collected (positive when short perp + positive funding)."""
    direction = 1 if is_short_perp else -1
    per_period = notional_usd * (funding_rate_pct_per_period / 100.0) * direction
    return per_period * periods
