"""FX rate models. Rates are 'units of local currency per 1 USD'."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NgnRates(BaseModel):
    """Nigeria three-rate display (FX-03): the official, parallel, and P2P rates
    diverge materially, so the user picks which to value their P&L at."""

    official: float | None = Field(default=None, description="CBN official USD/NGN")
    parallel: float | None = Field(default=None, description="Wise/parallel USD/NGN")
    p2p: float | None = Field(default=None, description="P2P market USD/NGN")


class RatesSnapshot(BaseModel):
    base: str = "USD"
    rates: dict[str, float] = Field(default_factory=dict, description="USD→local per currency")
    ngn: NgnRates = Field(default_factory=NgnRates)
    source: str = Field(description="which provider supplied the primary rates")
    fetched_at: datetime


class ConvertResponse(BaseModel):
    amount_usd: float
    currency: str
    rate: float
    amount_local: float
    ngn_rate_type: str | None = None
    fetched_at: datetime
