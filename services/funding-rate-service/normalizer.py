"""Normalize raw source-shaped funding rates to the shared FundingRate model.

Each source has its own JSON shape. This module exposes one normalizer per
source plus a helper that computes the annualized percentage from a single
period rate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.models.funding_rate import FundingRate
from shared.utils.exchange_normalizer import normalize_symbol  # used by from_ccxt

HOURS_PER_YEAR = 365 * 24


def annualize(rate_per_period: float, period_hours: float) -> float:
    """Convert a per-period rate (decimal) into an annualized percent."""
    if period_hours <= 0:
        return 0.0
    periods_per_year = HOURS_PER_YEAR / period_hours
    return rate_per_period * periods_per_year * 100.0


def from_coinglass(row: dict[str, Any]) -> FundingRate | None:
    """CoinGlass shape (per `/futures/funding_rate/chart`):
    {symbol, exchangeName, rate, nextFundingTime, ...}
    """
    try:
        exchange = row["exchangeName"].lower()
        asset = row["symbol"].upper()
        rate = float(row["rate"])
        period_hours = float(row.get("intervalHours", 8.0))
    except (KeyError, TypeError, ValueError):
        return None

    return FundingRate(
        exchange=exchange,
        asset=asset,
        symbol=f"{asset}/USD:PERP",
        rate_per_period=rate,
        period_hours=period_hours,
        annualized_pct=annualize(rate, period_hours),
        next_funding_time=_safe_dt(row.get("nextFundingTime")),
        observed_at=datetime.now(UTC),
        source="coinglass",
    )


def from_arbitrage_scanner(row: dict[str, Any]) -> FundingRate | None:
    """ArbitrageScanner.io shape: {exchange, base, rate_8h, next_funding_ts}"""
    try:
        exchange = row["exchange"].lower()
        asset = row["base"].upper()
        rate = float(row["rate_8h"])
        period_hours = 8.0
    except (KeyError, TypeError, ValueError):
        return None

    return FundingRate(
        exchange=exchange,
        asset=asset,
        symbol=f"{asset}/USD:PERP",
        rate_per_period=rate,
        period_hours=period_hours,
        annualized_pct=annualize(rate, period_hours),
        next_funding_time=_safe_dt(row.get("next_funding_ts")),
        observed_at=datetime.now(UTC),
        source="arbitrage_scanner",
    )


def from_ccxt(exchange: str, ccxt_funding_rate: dict[str, Any]) -> FundingRate | None:
    """CCXT shape: {symbol, fundingRate, fundingDatetime, ...}"""
    try:
        rate = float(ccxt_funding_rate["fundingRate"])
        symbol = ccxt_funding_rate["symbol"]
    except (KeyError, TypeError, ValueError):
        return None

    exchange_l = exchange.lower()
    canonical = normalize_symbol(exchange_l, symbol, "perp")
    asset = canonical.split("/", 1)[0]
    # CEX perps fund every 8h; Hyperliquid every 1h. CCXT doesn't always
    # surface the interval, so derive from exchange.
    period_hours = 1.0 if exchange_l == "hyperliquid" else 8.0

    return FundingRate(
        exchange=exchange_l,
        asset=asset,
        symbol=canonical,
        rate_per_period=rate,
        period_hours=period_hours,
        annualized_pct=annualize(rate, period_hours),
        next_funding_time=_safe_dt(ccxt_funding_rate.get("fundingTimestamp")),
        observed_at=datetime.now(UTC),
        source="ccxt",
    )


def _safe_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:  # milliseconds
        ts /= 1000.0
    return datetime.fromtimestamp(ts, tz=UTC)
