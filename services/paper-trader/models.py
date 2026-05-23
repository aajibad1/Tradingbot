from datetime import datetime

from pydantic import BaseModel, Field

from shared.models.opportunity import Opportunity


class SimulateRequest(BaseModel):
    opportunity: Opportunity
    long_reference_price: float = Field(gt=0.0)
    short_reference_price: float = Field(gt=0.0)
    long_book_depth_usd: float = Field(
        default=250_000.0,
        gt=0.0,
        description="Approx top-of-book USD depth for slippage modelling",
    )
    short_book_depth_usd: float = Field(default=250_000.0, gt=0.0)


class SimulateResponse(BaseModel):
    trade_id: str
    accepted: bool
    rejection_reason: str | None = None
    net_pnl_usd: float = 0.0
    gross_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    slippage_usd: float = 0.0
    funding_collected_usd: float = 0.0
    opened_at: datetime
    closed_at: datetime | None = None
    notes: str = ""
