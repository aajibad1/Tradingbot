from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TradeType(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class TradeStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    FAILED = "failed"


class TradeLeg(BaseModel):
    exchange: str
    side: str = Field(description="'buy' or 'sell'")
    asset: str
    size: float = Field(gt=0.0)
    fill_price: float = Field(gt=0.0)
    fee_usd: float = Field(ge=0.0)
    slippage_usd: float = Field(ge=0.0)
    filled_at: datetime


class Trade(BaseModel):
    id: str
    opportunity_id: str
    type: TradeType
    legs: list[TradeLeg]

    gross_pnl_usd: float
    net_pnl_usd: float
    status: TradeStatus
    opened_at: datetime
    closed_at: datetime | None = None
    funding_collected_usd: float = 0.0
    notes: str = ""
