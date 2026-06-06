"""Double-entry ledger operations + invariants.

Every operation writes ONE balanced transaction (postings sum to zero per asset).
Balances are derived, never stored. Overdrafts are refused. Money is Decimal.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import SYSTEM_TENANT, AccountType, Posting, Transaction, TxnKind


class LedgerError(Exception):
    pass


class InsufficientFunds(LedgerError):
    pass


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def balance(db: Session, tenant_id: str, asset: str,
            account_type: AccountType = AccountType.AVAILABLE) -> Decimal:
    """Sum of a tenant's postings for (asset, account_type). The only way to read
    a balance — it is always re-derivable from the postings."""
    total = db.execute(
        select(func.coalesce(func.sum(Posting.amount), 0)).where(
            Posting.tenant_id == tenant_id,
            Posting.asset == asset,
            Posting.account_type == account_type,
        )
    ).scalar_one()
    return _d(total)


def list_balances(db: Session, tenant_id: str) -> list[dict]:
    """All non-zero-touched assets for a tenant with available + reserved."""
    assets = db.execute(
        select(Posting.asset).where(Posting.tenant_id == tenant_id).distinct()
    ).scalars().all()
    out = []
    for asset in sorted(assets):
        out.append({
            "asset": asset,
            "available": float(balance(db, tenant_id, asset, AccountType.AVAILABLE)),
            "reserved": float(balance(db, tenant_id, asset, AccountType.RESERVED)),
        })
    return out


def _post(db: Session, kind: TxnKind, legs: list[tuple[str, AccountType, Decimal]],
          asset: str, detail: str) -> Transaction:
    """Write a transaction from (tenant, account_type, signed_amount) legs after
    asserting they net to zero — the double-entry guarantee."""
    if sum((amt for _, _, amt in legs), Decimal(0)) != Decimal(0):
        raise LedgerError("unbalanced transaction (postings must net to zero)")
    txn = Transaction(kind=kind, detail=detail)
    for tenant_id, acct, amt in legs:
        txn.postings.append(Posting(tenant_id=tenant_id, asset=asset, account_type=acct, amount=amt))
    db.add(txn)
    db.flush()
    return txn


def _require_positive(amount: Decimal) -> Decimal:
    amount = _d(amount)
    if amount <= 0:
        raise LedgerError("amount must be positive")
    return amount


def deposit(db: Session, tenant_id: str, asset: str, amount, detail: str = "") -> Transaction:
    amount = _require_positive(amount)
    return _post(db, TxnKind.DEPOSIT, [
        (tenant_id, AccountType.AVAILABLE, amount),
        (SYSTEM_TENANT, AccountType.EXTERNAL, -amount),
    ], asset, detail)


def withdraw(db: Session, tenant_id: str, asset: str, amount, detail: str = "") -> Transaction:
    amount = _require_positive(amount)
    if balance(db, tenant_id, asset, AccountType.AVAILABLE) < amount:
        raise InsufficientFunds(f"available {asset} < {amount}")
    return _post(db, TxnKind.WITHDRAW, [
        (tenant_id, AccountType.AVAILABLE, -amount),
        (SYSTEM_TENANT, AccountType.EXTERNAL, amount),
    ], asset, detail)


def reserve(db: Session, tenant_id: str, asset: str, amount, detail: str = "") -> Transaction:
    """Lock available funds behind an open position (available → reserved)."""
    amount = _require_positive(amount)
    if balance(db, tenant_id, asset, AccountType.AVAILABLE) < amount:
        raise InsufficientFunds(f"available {asset} < {amount}")
    return _post(db, TxnKind.RESERVE, [
        (tenant_id, AccountType.AVAILABLE, -amount),
        (tenant_id, AccountType.RESERVED, amount),
    ], asset, detail)


def release(db: Session, tenant_id: str, asset: str, amount, detail: str = "") -> Transaction:
    """Unlock reserved funds back to available (reserved → available)."""
    amount = _require_positive(amount)
    if balance(db, tenant_id, asset, AccountType.RESERVED) < amount:
        raise InsufficientFunds(f"reserved {asset} < {amount}")
    return _post(db, TxnKind.RELEASE, [
        (tenant_id, AccountType.RESERVED, -amount),
        (tenant_id, AccountType.AVAILABLE, amount),
    ], asset, detail)


def settle_pnl(db: Session, tenant_id: str, asset: str, pnl, detail: str = "") -> Transaction:
    """Book realized trading P&L to the tenant's available balance (signed).
    The counterparty is the platform PnL account, so the books stay balanced."""
    pnl = _d(pnl)
    if pnl == 0:
        raise LedgerError("pnl must be non-zero")
    return _post(db, TxnKind.SETTLE_PNL, [
        (tenant_id, AccountType.AVAILABLE, pnl),
        (SYSTEM_TENANT, AccountType.PNL, -pnl),
    ], asset, detail)
