# ADR-0002: Authentication/authorization — pluggable provider (Clerk) + tenant-scoped API keys

Status: Accepted (documents an existing, already-implemented decision)
Date: 2026-09-17
Owner: Security role

## Context
The contract requires OIDC-compatible auth with internal RBAC/ABAC (§5.1)
and tenant-scoped API keys with restricted scopes (§7.3). Two distinct
audiences exist: human dashboard users (core-api) and partner developers
calling the API plane (partner-auth).

## Decision
- **Human/dashboard auth**: `core-api/auth.py` implements a pluggable
  `AUTH_PROVIDER` seam. `AUTH_PROVIDER=clerk` verifies a Clerk session JWT
  via JWKS (OIDC-compatible); unset defaults to a local-bearer adapter for
  local development. Firebase is stubbed but not implemented
  (`raise HTTPException(501, "not yet wired")` — fails loud, not silently).
- **Partner/API-plane auth**: `partner-auth` issues tenant-scoped API keys,
  hashed at rest, with rotation and revocation support; `public-api-gateway`
  authenticates every request against it before metering/proxying.
- **RBAC**: enforced in `core-api` per docs/09's "RBAC and tenant isolation"
  control; scopes enforced at the gateway for partner keys (verified this
  session — an onramp-only key gets 403 on an offramp call in
  `scripts/api_plane_smoke.sh`).

## Alternatives considered
- Single unified auth provider for both humans and API partners — rejected:
  API keys and OIDC sessions have fundamentally different rotation,
  scoping, and revocation semantics; conflating them would weaken both.
- Build a custom auth server instead of Clerk — rejected: no product reason
  to own identity infrastructure; Clerk wiring needs only a real project
  (JWKS + secret) from the user to go live (tracked in memory as a known
  external dependency, not a code gap).

## Consequences
- Clerk production wiring is blocked on the user supplying real credentials
  — this is correctly a human-supplied-secret decision per the contract's
  own escalation rule (§2.1: "requires production secrets").
- Two auth systems (core-api sessions, partner-auth API keys) must both be
  kept tenant-safe independently; `shared/tenant.py` is the one shared
  primitive both build on.

## Revisit when
A real Clerk project is provisioned (unblocks prod auth), or a second
human-identity provider is required (e.g. enterprise SSO for a large
customer).
