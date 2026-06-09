from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from shared.tenant import DEFAULT_TENANT


class StrategyType(str, Enum):
    FUNDING_RATE_ARB = "funding_rate_arb"
    SPOT_PERP_BASIS = "spot_perp_basis"
    CROSS_EXCHANGE = "cross_exchange"
    TRIANGULAR = "triangular"
    # Directional (NOT market-neutral): consumes the risk-engine directional
    # budget. The hybrid "satellite" sleeve — everything else is the neutral core.
    DIRECTIONAL = "directional"


class Opportunity(BaseModel):
    """Scored arbitrage opportunity emitted by opportunity-engine."""

    id: str = Field(description="UUID assigned at detection time")
    user_id: str = Field(default=DEFAULT_TENANT, description="Owning tenant/user")
    strategy: StrategyType
    asset: str = Field(description="Base symbol, e.g. 'BTC'")
    long_exchange: str
    short_exchange: str

    gross_spread_bps: float
    trading_fees_bps: float
    slippage_estimate_bps: float
    funding_rate_annualized_pct: float = 0.0
    net_edge_bps: float = Field(description="gross + funding - fees - slippage - buffer")

    confidence_score: float = Field(ge=0.0, le=1.0)
    recommended_size_usd: float = Field(gt=0.0)
    min_hold_hours: float = Field(ge=0.0)

    detected_at: datetime
    execute: bool = Field(default=False, description="Only True after risk-engine approval")
    rejection_reason: str | None = None
    # For DIRECTIONAL strategy only: 'long' or 'short' (the single perp leg's side).
    # None for market-neutral strategies (both legs implied by long/short exchange).
    direction: str | None = None

    # Reference prices at detection, carried so the execution simulator prices
    # fills off the REAL market instead of a hardcoded placeholder. Optional for
    # backward compatibility; consumers fall back when absent.
    long_reference_price: float | None = Field(default=None, gt=0.0)
    short_reference_price: float | None = Field(default=None, gt=0.0)
