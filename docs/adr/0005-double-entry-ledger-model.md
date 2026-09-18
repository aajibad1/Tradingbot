# ADR-0005: Double-entry ledger — balanced postings, derived balances, no mutation

Status: Accepted (documents an existing, already-implemented decision)
Date: 2026-09-17
Owner: Ledger/Reconciliation role

## Context
The contract's §6.2 ledger invariants require: every journal entry balances
(debits == credits), entries are immutable after posting, corrections are
compensating entries not mutation/deletion, every posting is tenant-scoped
and attributable to a source event, and posting is transactionally safe and
idempotent.

## Decision
`services/accounts-service/ledger.py` already implements this design:
- `_post()` asserts `sum(amounts) == Decimal(0)` before writing any
  transaction — an unbalanced set of legs raises `LedgerError` and nothing
  is written. This is the double-entry guarantee, enforced in code, not
  just convention.
- Balances are **never stored** — `balance()` is `SUM(Posting.amount)`
  filtered by `(tenant_id, asset, account_type)`, computed on every read.
  There is no mutable balance column to drift out of sync with the
  postings that are supposed to explain it.
- Postings are rows in an append-only `Posting` table tied to an immutable
  `Transaction` parent (`Transaction.postings`) — there is no update/delete
  path in `ledger.py`. A correction is a new transaction with the opposite
  legs (matching the contract's "compensating/reversal entries, never
  mutation or deletion").
- `AccountType` (AVAILABLE / RESERVED, per `db.py`) gives the
  available-vs-reserved balance separation the contract requires; overdraft
  is refused (`InsufficientFunds`).

## Alternatives considered
- Store a running balance column with optimistic locking — rejected: reopens
  exactly the "balance drifts from the postings that justify it" class of
  bug the derived-balance design eliminates by construction. Query cost at
  current transaction volume does not justify the risk.

## Consequences
- Every new money-moving code path (in any service) must post through
  `accounts-service`, never mutate a balance field directly — `wallet-service`
  is the one current exception (ADR-0004) and is tracked as a gap, not a
  second valid pattern.
- Reconciliation (`settlement-status`) reads from this ledger as the
  source of truth for "did this actually happen," not from a service's own
  optimistic status field.

## Revisit when
Transaction volume makes `SUM()`-per-read balance computation a measured
performance problem — the standard mitigation (materialized/cached balance
with an invalidation-on-post trigger) is a performance optimization on top
of this model, not a replacement for it.
