from datetime import datetime

from pydantic import BaseModel, Field


class FundingRate(BaseModel):
    """Normalized funding rate snapshot for a perp on one venue."""

    exchange: str = Field(description="Venue name, lowercase: 'kraken', 'hyperliquid', ...")
    asset: str = Field(description="Base asset, e.g. 'BTC'")
    symbol: str = Field(description="Canonical perp symbol, e.g. 'BTC/USD:PERP'")

    rate_per_period: float = Field(
        description="Funding rate for one period as a decimal (0.0001 = 0.01%)"
    )
    period_hours: float = Field(
        gt=0.0,
        description="Length of one funding period in hours (1 for HL, 8 for CEX)",
    )
    annualized_pct: float = Field(
        description="Annualized funding rate as a percentage (rate * periods_per_year * 100)"
    )

    next_funding_time: datetime | None = None
    observed_at: datetime
    source: str = Field(description="'coinglass', 'arbitrage_scanner', 'ccxt', ...")
