# ADR-0004: Money representation — Decimal is canonical; float is a tracked sandbox gap

Status: Accepted, with one open remediation item
Date: 2026-09-17
Owner: Ledger/Reconciliation role

## Context
The contract's §6.1 is explicit: never use floating-point for money; store
integer minor units (or fixed-precision decimal) with explicit currency.
This repo has two different money representations in production code today.

## Decision
`accounts-service`'s `Posting.amount` (`Numeric(38, 8)` at the DB layer,
Python `Decimal` at the application layer — `ledger.py`'s `_d()` helper
coerces every input to `Decimal` before it touches a posting) is the
**canonical** money representation for anything that is, or will become, a
real balance. This is accepted as-is: it already satisfies the contract's
invariant.

`wallet-service` currently represents balances as Python `float`
(`w["balance"]: float`, `_round()`). This is a **known, explicitly
documented gap**, not a silent bug — the service's own module docstring
says "SANDBOX: ... amounts are float rounded for the sandbox; production
uses Decimal." This ADR converts that comment into a tracked decision:
`wallet-service` must be migrated to `Decimal` (either by adopting
`accounts-service`'s ledger directly, or by mirroring its `Numeric`/
`Decimal` pattern) **before** any live-money milestone. See
`docs/STATUS.md` Backlog for the tracking issue.

## Alternatives considered
- Leave `wallet-service` on float indefinitely since it's sandbox-only —
  rejected: the contract's invariant is a hard rule, not a "before
  go-live" checklist item alone; a float-based balance API sets the wrong
  precedent for anyone extending it before the migration lands, and
  rounding-error accumulation is exactly the class of bug that's invisible
  in a demo and catastrophic in production.

## Consequences
- Every new balance-touching code path must use `Decimal`, never `float`
  — code review should treat a new `float` balance field as a
  `blocking:` comment per the contract's PR review standard (§10.4).
- `accounts-service` and `wallet-service` currently maintain two
  independent balance representations for what is conceptually one
  concern (a tenant's money). `docs/STATUS.md`'s next-prioritized-work
  item #3 proposes collapsing this to one authority.

## Revisit when
The `wallet-service` → `accounts-service` (or equivalent Decimal) migration
lands — this ADR's "one open remediation item" should be marked resolved
and the status changed to fully Accepted.
