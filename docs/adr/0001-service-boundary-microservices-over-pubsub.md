# ADR-0001: Service boundary — independent microservices over Pub/Sub, not a modular monolith

Status: Accepted (documents an existing, already-implemented decision)
Date: 2026-09-17
Owner: Staff Engineer role

## Context
The operating contract's default recommendation (§5) is a modular monolith
with enforced module boundaries, on the grounds that microservices add
premature operational complexity. This repo predates the contract and
already exists as ~40 independent Python services communicating over GCP
Pub/Sub, each with its own `Dockerfile`/`requirements.txt`, deployed as
separate Cloud Run services (`infra/terraform/main.tf`).

## Decision
Keep the existing per-service microservice boundary. Do not collapse
services into a modular monolith to match the contract's generic default.
The contract itself instructs: "use the repository's existing standards if
present" (§5.1) — this repo has load-bearing existing standards.

Each service still enforces the modular-monolith *spirit* of the contract:
a service depends only on `shared/` and Pub/Sub messages, never on another
service's code (CLAUDE.md's own invariant) — the same "clearly enforced
module boundary" goal, implemented via process isolation instead of
in-process module boundaries.

## Alternatives considered
- Collapse everything into one NestJS/Fastify modular monolith per the
  contract's baseline stack — rejected: would require rewriting ~40
  working, tested Python services in TypeScript for no functional gain,
  and would violate "use existing standards if present."
- Collapse only the Africa-plane (API plane) services into one Python
  modular monolith — rejected for now: no measured scaling/deployment
  problem exists that motivates the migration cost; revisit if the
  API-plane's per-service Cloud Run cold-start/cost profile becomes a
  problem (see Revisit below).

## Consequences
- Cross-service changes require touching the `Topic` enum
  (`shared/pubsub/publisher.py`) and Terraform provisioning together — this
  session's `arb-signals`/`arb-funding-rates-ledger` bugs are the direct
  cost of that split-source-of-truth design; mitigated by the audit habit
  established this session, not eliminated by the architecture.
- Each service can be deployed, scaled, and rolled back independently —
  valuable for the payments plane specifically, where a `webhook-service`
  incident must never be able to take down `accounts-service`.
- New engineers must learn a message-bus contract instead of navigating one
  codebase; CLAUDE.md exists specifically to offset this cost.

## Revisit when
A measured scaling, security, or deployment reason demands further
splitting or, conversely, cost/ops overhead data shows the API-plane
services would be cheaper and simpler consolidated. Not before then.
