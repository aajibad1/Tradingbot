"""accounts-service — the internal money ledger (managed custody, paper mode).

Per-tenant balances via a double-entry ledger. No real funds move until licensing
clears (docs/REGULATORY_BRIEF.md); this is the accounting system-of-record that
the execution path reserves against and settles PnL into.

Endpoints:
  GET  /healthz
  GET  /v1/accounts/{tenant}/balances?asset=USD
  POST /v1/accounts/{tenant}/deposit      {asset, amount}
  POST /v1/accounts/{tenant}/withdraw     {asset, amount}
  POST /v1/accounts/{tenant}/reserve      {asset, amount}
  POST /v1/accounts/{tenant}/release      {asset, amount}
  POST /v1/accounts/{tenant}/settle-pnl   {asset, pnl}
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import ledger
from db import AccountType, get_session, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("accounts-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="accounts-service", version="0.1.0", lifespan=lifespan)


class Amount(BaseModel):
    asset: str
    amount: Decimal


class Pnl(BaseModel):
    asset: str
    pnl: Decimal


class Balances(BaseModel):
    tenant_id: str
    asset: str
    available: Decimal
    reserved: Decimal


def _op(fn, db: Session, tenant: str, asset: str, value, detail: str):
    try:
        fn(db, tenant, asset, value, detail=detail)
        db.commit()
    except ledger.InsufficientFunds as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ledger.LedgerError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


def _balances(db: Session, tenant: str, asset: str) -> Balances:
    return Balances(
        tenant_id=tenant, asset=asset,
        available=ledger.balance(db, tenant, asset, AccountType.AVAILABLE),
        reserved=ledger.balance(db, tenant, asset, AccountType.RESERVED),
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/accounts/{tenant}/balances", response_model=Balances)
def get_balances(tenant: str, asset: str, db: Session = Depends(get_session)) -> Balances:
    return _balances(db, tenant, asset)


@app.post("/v1/accounts/{tenant}/deposit", response_model=Balances)
def deposit(tenant: str, body: Amount, db: Session = Depends(get_session)) -> Balances:
    _op(ledger.deposit, db, tenant, body.asset, body.amount, "api.deposit")
    return _balances(db, tenant, body.asset)


@app.post("/v1/accounts/{tenant}/withdraw", response_model=Balances)
def withdraw(tenant: str, body: Amount, db: Session = Depends(get_session)) -> Balances:
    _op(ledger.withdraw, db, tenant, body.asset, body.amount, "api.withdraw")
    return _balances(db, tenant, body.asset)


@app.post("/v1/accounts/{tenant}/reserve", response_model=Balances)
def reserve(tenant: str, body: Amount, db: Session = Depends(get_session)) -> Balances:
    _op(ledger.reserve, db, tenant, body.asset, body.amount, "api.reserve")
    return _balances(db, tenant, body.asset)


@app.post("/v1/accounts/{tenant}/release", response_model=Balances)
def release(tenant: str, body: Amount, db: Session = Depends(get_session)) -> Balances:
    _op(ledger.release, db, tenant, body.asset, body.amount, "api.release")
    return _balances(db, tenant, body.asset)


@app.post("/v1/accounts/{tenant}/settle-pnl", response_model=Balances)
def settle_pnl(tenant: str, body: Pnl, db: Session = Depends(get_session)) -> Balances:
    _op(ledger.settle_pnl, db, tenant, body.asset, body.pnl, "api.settle_pnl")
    return _balances(db, tenant, body.asset)
