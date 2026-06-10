"""Wire schemas for the on-ramp API (docs/06 §On-ramp, docs/07 funding entities).

On-ramp = fiat → stablecoin (e.g. NGN → USDC). These are the normalized request/
response shapes the orchestrator exposes; the underlying provider formats are
hidden behind the sandbox provider (and, later, real rail connectors).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from shared.http import Status


class QuoteRequest(BaseModel):
    source_currency: str = Field(description="Fiat ISO code, e.g. NGN, KES, GHS")
    dest_asset: str = Field(description="Stablecoin, e.g. USDC, USDT")
    amount: float = Field(gt=0, description="Amount in source_currency")
    corridor: str | None = Field(default=None, description="Optional explicit corridor id")


class Quote(BaseModel):
    quote_id: str
    source_currency: str
    dest_asset: str
    amount: float
    fee: float = Field(description="Fee in source_currency")
    rate: float = Field(description="source_currency per 1 dest_asset")
    dest_amount: float = Field(description="Net amount delivered in dest_asset")
    expires_at: datetime


class OrderRequest(BaseModel):
    source_currency: str
    dest_asset: str
    amount: float = Field(gt=0)
    destination_wallet: str = Field(description="Where the stablecoin is delivered")
    quote_id: str | None = Field(default=None, description="Optional prior quote to honor")
    corridor: str | None = None


class Order(BaseModel):
    id: str
    status: str = Field(description="One of shared.http.Status (pending→processing→completed/failed)")
    source_currency: str
    dest_asset: str
    amount: float
    fee: float
    dest_amount: float
    destination_wallet: str
    tenant_id: str | None = None
    correlation_id: str
    created_at: datetime
    updated_at: datetime

    def is_terminal(self) -> bool:
        return self.status in Status.TERMINAL
