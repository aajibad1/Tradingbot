"""Internal accounts ledger — double-entry, the system-of-record for per-tenant
money in the managed-custody model.

Every movement is a *transaction* with ≥2 *postings* that sum to zero per asset
(double-entry). A balance is just the sum of a tenant's postings for an asset and
account type — never a mutable counter — so the ledger can always be re-derived
and reconciled against the platform's real exchange balances.

PAPER MODE: this records intended movements; no real funds are touched until
licensing clears (see docs/REGULATORY_BRIEF.md). DATABASE_URL selects Postgres
(prod) or SQLite (dev/test, default).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Numeric, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

# The platform's own books — the counterparty for deposits/withdrawals/PnL so
# every transaction balances. Distinct from any real tenant id.
SYSTEM_TENANT = "platform"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AccountType(str, Enum):
    AVAILABLE = "available"   # tenant funds free to deploy
    RESERVED = "reserved"     # tenant funds locked behind an open position
    EXTERNAL = "external"     # system: cash in/out of the platform boundary
    PNL = "pnl"               # system: realized trading P&L source/sink


class TxnKind(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    RESERVE = "reserve"
    RELEASE = "release"
    SETTLE_PNL = "settle_pnl"


class Base(DeclarativeBase):
    pass


def _enum_col(enum_cls):
    return SAEnum(enum_cls, native_enum=False, values_callable=lambda e: [m.value for m in e])


class Transaction(Base):
    __tablename__ = "ledger_transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[TxnKind] = mapped_column(_enum_col(TxnKind))
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    postings: Mapped[list["Posting"]] = relationship(
        back_populates="txn", cascade="all, delete-orphan"
    )


class Posting(Base):
    __tablename__ = "ledger_postings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    txn_id: Mapped[int] = mapped_column(ForeignKey("ledger_transactions.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    asset: Mapped[str] = mapped_column(String(16), index=True)          # 'USD','USDT','BTC',...
    account_type: Mapped[AccountType] = mapped_column(_enum_col(AccountType))
    # Numeric, not float — money. Signed: + credits the account, - debits it.
    amount: Mapped[float] = mapped_column(Numeric(38, 8))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    txn: Mapped[Transaction] = relationship(back_populates="postings")


_engine = None
_Session: sessionmaker | None = None


def init_db(database_url: str | None = None):
    global _engine, _Session
    url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./accounts.db")
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    _engine = create_engine(url, future=True, **kwargs)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)
    return _engine


def get_session():
    if _Session is None:
        init_db()
    assert _Session is not None
    db = _Session()
    try:
        yield db
    finally:
        db.close()
