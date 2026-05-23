from datetime import datetime

from pydantic import BaseModel, Field


class OrderBookLevel(BaseModel):
    price: float = Field(gt=0.0)
    size: float = Field(ge=0.0)


class ExchangeTick(BaseModel):
    """Normalized top-of-book tick from any exchange."""

    exchange: str
    symbol: str = Field(description="Normalized symbol, e.g. 'BTC/USD'")
    asset: str = Field(description="Base asset, e.g. 'BTC'")
    bid: float = Field(gt=0.0)
    ask: float = Field(gt=0.0)
    bid_size: float = Field(ge=0.0)
    ask_size: float = Field(ge=0.0)
    last: float | None = None
    timestamp: datetime
    instrument_type: str = Field(default="spot", description="'spot' or 'perp'")


class OrderBookSnapshot(BaseModel):
    """Top-N order book snapshot used for slippage estimation."""

    exchange: str
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime
