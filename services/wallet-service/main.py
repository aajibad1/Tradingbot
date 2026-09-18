"""wallet-service — per-tenant wallets + balances (docs/06 Wallets & Balances).

A sandbox custodial ledger view: each tenant has wallets (one per asset), with a
balance adjusted by credits/debits (e.g. an on-ramp completion credits stablecoin,
a payout debits it). Debits are overdraft-protected. Read endpoints surface wallets
and aggregated balances; mutation is via an internal adjust endpoint.

SANDBOX: balances are an in-memory ledger (single-instance) — production would back
this with the double-entry accounts-service / Cloud SQL and real custody (see
docs/adr/0004-money-representation.md "Consequences": two independent balance
representations exist for one concern; this service is not yet the double-entry
authority accounts-service is). Money is Decimal, on the wire AND internally —
docs/adr/0004 is explicit that float is never acceptable for money, sandbox or
not. Decimal round-trips through FastAPI's default JSON encoder as a float
(precision-losing), so every response serializes balances as strings.

Endpoints (docs/06):
  GET  /healthz
  POST /v1/wallets                      {tenant_id, asset}
  GET  /v1/wallets?tenant=
  GET  /v1/wallets/{id}
  POST /v1/wallets/{id}/adjust          {amount, reason}   (credit >0 / debit <0)
  GET  /v1/balances?tenant=
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator

from shared.http import APIError, install_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("wallet-service")

PRODUCER = "wallet-service"

# 8 decimal places — matches the precision every other Decimal-money path in
# this repo uses (accounts-service's Numeric(38, 8)). Banker's rounding
# (ROUND_HALF_EVEN) avoids the small systematic upward bias round-half-up
# would introduce over many postings.
_QUANT = Decimal("0.00000001")

# wallet_id -> wallet dict (in-memory sandbox ledger). "balance" is Decimal.
_wallets: dict[str, dict] = {}


app = FastAPI(title="wallet-service", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(_QUANT, rounding=ROUND_HALF_EVEN)


def _wallet_out(w: dict) -> dict:
    """Wire representation — balance as a fixed-point string so FastAPI's
    default JSON encoder (which casts Decimal -> float) can never silently
    reintroduce the float-precision problem this migration removes.

    format(x, "f") is required, not str(x): Decimal.__str__ switches to
    scientific notation once the adjusted exponent is < -6 (e.g. Decimal
    "0.00000002" -> "2E-8") — exactly the sub-micro-unit range a stablecoin
    balance can legitimately sit in, and exactly the shape this migration
    exists to keep fixed-point on the wire."""
    return {**w, "balance": format(w["balance"], "f")}


class WalletCreate(BaseModel):
    tenant_id: str
    asset: str = Field(description="Asset/currency held, e.g. USDC, NGN")


class Adjust(BaseModel):
    amount: Decimal = Field(description="Positive = credit, negative = debit")
    reason: str = Field(default="", description="Audit reason, e.g. 'funding ord_123 completed'")

    @field_validator("amount", mode="before")
    @classmethod
    def _reject_float_input(cls, v: object) -> object:
        # A JSON number like 99.1 arrives as a Python float before Pydantic
        # converts it — by then the precision loss already happened. Accept
        # only int/str/Decimal on the wire; float is exactly the mistake
        # this migration exists to close off, including at the request body.
        if isinstance(v, float):
            raise ValueError("amount must be sent as a string or integer, not a JSON float")
        return v


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/wallets")
def create_wallet(req: WalletCreate) -> dict[str, Any]:
    wid = f"wlt_{uuid.uuid4().hex[:16]}"
    now = _now().isoformat()
    _wallets[wid] = {
        "id": wid, "tenant_id": req.tenant_id, "asset": req.asset.upper(),
        "balance": _quantize(Decimal(0)), "created_at": now, "updated_at": now,
    }
    return _wallet_out(_wallets[wid])


@app.get("/v1/wallets")
def list_wallets(tenant: str | None = None) -> dict[str, Any]:
    out = [_wallet_out(w) for w in _wallets.values() if tenant is None or w["tenant_id"] == tenant]
    return {"wallets": out, "count": len(out)}


def _get_wallet(wallet_id: str) -> dict[str, Any]:
    w = _wallets.get(wallet_id)
    if w is None:
        raise APIError("wallet_not_found", f"wallet {wallet_id} not found", http_status=404)
    return w


@app.get("/v1/wallets/{wallet_id}")
def get_wallet(wallet_id: str) -> dict[str, Any]:
    return _wallet_out(_get_wallet(wallet_id))


@app.post("/v1/wallets/{wallet_id}/adjust")
def adjust(wallet_id: str, req: Adjust) -> dict[str, Any]:
    w = _get_wallet(wallet_id)
    if req.amount == 0:
        raise APIError("invalid_amount", "amount must be non-zero", http_status=422)
    try:
        new_balance = _quantize(w["balance"] + req.amount)
    except InvalidOperation as exc:
        raise APIError("invalid_amount", f"amount could not be applied: {exc}", http_status=422) from exc
    if new_balance < 0:
        raise APIError(
            "insufficient_funds",
            f"debit {req.amount} exceeds balance {w['balance']} in wallet {wallet_id}",
            http_status=409,
        )
    w["balance"] = new_balance
    w["updated_at"] = _now().isoformat()
    _wallets[wallet_id] = w
    logger.info("wallet %s %+.8f %s → %.8f (%s)", wallet_id, req.amount, w["asset"],
                new_balance, req.reason)
    return _wallet_out(w)


@app.get("/v1/balances")
def balances(tenant: str) -> dict[str, Any]:
    """Aggregate balances per asset across a tenant's wallets."""
    by_asset: dict[str, Decimal] = {}
    for w in _wallets.values():
        if w["tenant_id"] == tenant:
            by_asset[w["asset"]] = _quantize(by_asset.get(w["asset"], Decimal(0)) + w["balance"])
    return {"tenant_id": tenant, "balances": {asset: str(amt) for asset, amt in by_asset.items()}}
