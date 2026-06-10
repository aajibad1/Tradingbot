"""wallet-service — per-tenant wallets + balances (docs/06 Wallets & Balances).

A sandbox custodial ledger view: each tenant has wallets (one per asset), with a
balance adjusted by credits/debits (e.g. an on-ramp completion credits stablecoin,
a payout debits it). Debits are overdraft-protected. Read endpoints surface wallets
and aggregated balances; mutation is via an internal adjust endpoint.

SANDBOX: balances are an in-memory ledger (single-instance). Production would back
this with the double-entry accounts-service / Cloud SQL and real custody. Money
amounts are float rounded for the sandbox; production uses Decimal.

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
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from shared.http import APIError, install_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("wallet-service")

PRODUCER = "wallet-service"

# wallet_id -> wallet dict (in-memory sandbox ledger).
_wallets: dict[str, dict] = {}


app = FastAPI(title="wallet-service", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _round(x: float) -> float:
    return round(x + 1e-12, 8)


class WalletCreate(BaseModel):
    tenant_id: str
    asset: str = Field(description="Asset/currency held, e.g. USDC, NGN")


class Adjust(BaseModel):
    amount: float = Field(description="Positive = credit, negative = debit")
    reason: str = Field(default="", description="Audit reason, e.g. 'funding ord_123 completed'")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/wallets")
def create_wallet(req: WalletCreate) -> dict[str, Any]:
    wid = f"wlt_{uuid.uuid4().hex[:16]}"
    now = _now().isoformat()
    _wallets[wid] = {
        "id": wid, "tenant_id": req.tenant_id, "asset": req.asset.upper(),
        "balance": 0.0, "created_at": now, "updated_at": now,
    }
    return _wallets[wid]


@app.get("/v1/wallets")
def list_wallets(tenant: str | None = None) -> dict[str, Any]:
    out = [w for w in _wallets.values() if tenant is None or w["tenant_id"] == tenant]
    return {"wallets": out, "count": len(out)}


def _get_wallet(wallet_id: str) -> dict[str, Any]:
    w = _wallets.get(wallet_id)
    if w is None:
        raise APIError("wallet_not_found", f"wallet {wallet_id} not found", http_status=404)
    return w


@app.get("/v1/wallets/{wallet_id}")
def get_wallet(wallet_id: str) -> dict[str, Any]:
    return _get_wallet(wallet_id)


@app.post("/v1/wallets/{wallet_id}/adjust")
def adjust(wallet_id: str, req: Adjust) -> dict[str, Any]:
    w = _get_wallet(wallet_id)
    if req.amount == 0:
        raise APIError("invalid_amount", "amount must be non-zero", http_status=422)
    new_balance = _round(w["balance"] + req.amount)
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
    return w


@app.get("/v1/balances")
def balances(tenant: str) -> dict[str, Any]:
    """Aggregate balances per asset across a tenant's wallets."""
    by_asset: dict[str, float] = {}
    for w in _wallets.values():
        if w["tenant_id"] == tenant:
            by_asset[w["asset"]] = _round(by_asset.get(w["asset"], 0.0) + w["balance"])
    return {"tenant_id": tenant, "balances": by_asset}
