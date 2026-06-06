"""accounts-service ledger tests — money invariants (SQLite, no cloud deps).

Run: PYTHONPATH=.:services/accounts-service python3 -m pytest services/accounts-service/tests/ -v
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pytest


@pytest.fixture()
def db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import db as dbmod
    dbmod.init_db(f"sqlite:///{path}")
    session = next(dbmod.get_session())
    yield session
    session.close()
    os.unlink(path)


def _bal(db, tenant, asset, kind):
    import ledger
    from db import AccountType
    return ledger.balance(db, tenant, asset, AccountType(kind))


def test_deposit_credits_available(db):
    import ledger
    ledger.deposit(db, "t1", "USD", 1000)
    db.commit()
    assert _bal(db, "t1", "USD", "available") == Decimal("1000")


def test_every_transaction_nets_to_zero(db):
    import ledger
    from db import Posting
    ledger.deposit(db, "t1", "USD", 500)
    ledger.reserve(db, "t1", "USD", 200)
    ledger.settle_pnl(db, "t1", "USD", Decimal("12.5"))
    db.commit()
    # Global invariant: across ALL postings for the asset, the books balance.
    total = sum((p.amount for p in db.query(Posting).filter_by(asset="USD")), Decimal(0))
    assert total == Decimal("0")


def test_withdraw_refused_on_insufficient_funds(db):
    import ledger
    ledger.deposit(db, "t1", "USD", 100)
    db.commit()
    with pytest.raises(ledger.InsufficientFunds):
        ledger.withdraw(db, "t1", "USD", 150)


def test_reserve_moves_available_to_reserved(db):
    import ledger
    ledger.deposit(db, "t1", "USD", 1000)
    ledger.reserve(db, "t1", "USD", 300)
    db.commit()
    assert _bal(db, "t1", "USD", "available") == Decimal("700")
    assert _bal(db, "t1", "USD", "reserved") == Decimal("300")


def test_release_returns_reserved_to_available(db):
    import ledger
    ledger.deposit(db, "t1", "USD", 1000)
    ledger.reserve(db, "t1", "USD", 300)
    ledger.release(db, "t1", "USD", 300)
    db.commit()
    assert _bal(db, "t1", "USD", "available") == Decimal("1000")
    assert _bal(db, "t1", "USD", "reserved") == Decimal("0")


def test_cannot_reserve_more_than_available(db):
    import ledger
    ledger.deposit(db, "t1", "USD", 100)
    db.commit()
    with pytest.raises(ledger.InsufficientFunds):
        ledger.reserve(db, "t1", "USD", 101)


def test_settle_pnl_positive_and_negative(db):
    import ledger
    ledger.deposit(db, "t1", "USD", 1000)
    ledger.settle_pnl(db, "t1", "USD", Decimal("50"))     # gain
    ledger.settle_pnl(db, "t1", "USD", Decimal("-20"))    # loss
    db.commit()
    assert _bal(db, "t1", "USD", "available") == Decimal("1030")


def test_positive_amount_enforced(db):
    import ledger
    with pytest.raises(ledger.LedgerError):
        ledger.deposit(db, "t1", "USD", 0)
    with pytest.raises(ledger.LedgerError):
        ledger.deposit(db, "t1", "USD", -5)


def test_list_balances_covers_all_assets(db):
    import ledger
    ledger.deposit(db, "t1", "USD", 1000)
    ledger.deposit(db, "t1", "USDT", 500)
    ledger.reserve(db, "t1", "USD", 300)
    db.commit()
    rows = {b["asset"]: b for b in ledger.list_balances(db, "t1")}
    assert rows["USD"]["available"] == 700.0 and rows["USD"]["reserved"] == 300.0
    assert rows["USDT"]["available"] == 500.0
    assert ledger.list_balances(db, "other") == []


def test_balances_are_isolated_per_tenant(db):
    import ledger
    ledger.deposit(db, "t1", "USD", 100)
    ledger.deposit(db, "t2", "USD", 250)
    db.commit()
    assert _bal(db, "t1", "USD", "available") == Decimal("100")
    assert _bal(db, "t2", "USD", "available") == Decimal("250")
