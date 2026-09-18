# ADR-0003: Tenant isolation — namespaced keys/rows, not per-tenant infrastructure

Status: Accepted (documents an existing, already-implemented decision)
Date: 2026-09-17
Owner: Security role

## Context
The contract requires tenant isolation enforced in both application
authorization and database access patterns (§6.3), with tests proving
isolation (§9's financial scenario matrix: "cross-tenant record access
attempt → denied, logged, no data disclosure").

## Decision
Tenant isolation is enforced by namespacing, not physical separation:
- `shared/tenant.py` provides the canonical namespacing helpers
  (`risk_key`, `exchange_secret_id`, `eligibility_key`) — every Redis key
  and Secret Manager reference a tenant-scoped identifier is derived from
  one function, not ad hoc string formatting per call site.
- `accounts-service` ledger postings are tenant-scoped rows
  (`Posting.tenant_id`); balance queries always filter by tenant — there is
  no code path that reads a balance without a tenant filter.
- `core-api` enforces RBAC at the request layer before any tenant-scoped
  query executes.
- DEFAULT_TENANT exists as an explicit sentinel for pre-multi-tenancy flows
  (the original single-tenant trading core), not as an isolation bypass.

## Alternatives considered
- Per-tenant database/schema — rejected: operationally expensive at this
  stage, and Postgres row-level security plus consistent namespacing gives
  equivalent isolation guarantees for the current tenant count and threat
  model.
- Per-tenant Redis instance — rejected for the same reason; namespaced keys
  with `risk_key(tenant_id)` give the same isolation without the
  infrastructure multiplication.

## Consequences
- Every new Redis/Secret Manager access point MUST go through
  `shared/tenant.py`'s helpers — a call site that hand-rolls a key string
  is a tenant-isolation bug waiting to happen. This is a code-review
  invariant, not something the type system enforces.
- Cross-tenant isolation tests exist for `risk-engine`
  (`test_multi_tenancy.py`) and should be required for any new
  tenant-scoped service (contract §9.2 financial scenario matrix item).

## Revisit when
Any customer's compliance/contractual requirements demand physical
(database- or infrastructure-level) tenant separation — likely a large
enterprise or regulated-partner requirement, not a default posture.
