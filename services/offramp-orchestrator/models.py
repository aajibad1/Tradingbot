"""Wire schemas for the off-ramp API (docs/06 §Off-ramp, docs/07 payout entities).

Off-ramp = stablecoin → fiat (e.g. USDC → NGN): a payout to a bank/mobile-money
account. Normalized request/response shapes; provider formats are hidden behind
the sandbox provider (and, later, real payout-rail connectors).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from shared.http import Status


class QuoteRequest(BaseModel):
    source_asset: str = Field(description="Stablecoin, e.g. USDC, USDT")
    dest_currency: str = Field(description="Fiat ISO code, e.g. NGN, KES, GHS")
    amount: float = Field(gt=0, description="Amount in source_asset (stablecoin)")
    corridor: str | None = Field(default=None, description="Optional explicit corridor id")


class Quote(BaseModel):
    quote_id: str
    source_asset: str
    dest_currency: str
    amount: float
    fee: float = Field(description="Fee in source_asset")
    rate: float = Field(description="dest_currency per 1 source_asset")
    dest_amount: float = Field(description="Net amount delivered in dest_currency (fiat)")
    expires_at: datetime


class OrderRequest(BaseModel):
    source_asset: str
    dest_currency: str
    amount: float = Field(gt=0)
    destination_account: str = Field(description="Bank / mobile-money account the fiat lands in")
    quote_id: str | None = Field(default=None, description="Optional prior quote to honor")
    corridor: str | None = None


class Order(BaseModel):
    id: str
    status: str = Field(description="One of shared.http.Status (pending→processing→completed/failed)")
    source_asset: str
    dest_currency: str
    amount: float
    fee: float
    dest_amount: float
    destination_account: str
    tenant_id: str | None = None
    correlation_id: str
    created_at: datetime
    updated_at: datetime

    def is_terminal(self) -> bool:
        return self.status in Status.TERMINAL
