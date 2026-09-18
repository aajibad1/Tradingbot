# ADR-0006: Event/outbox/idempotency model — Topic enum + EventEnvelope + per-service idempotency stores

Status: Accepted (documents an existing, already-implemented decision; one
pattern gap fixed this session)
Date: 2026-09-17
Owner: Staff Engineer role

## Context
The contract requires a canonical event envelope and transactional outbox
(§8 Milestone 1), and an idempotency story for both API requests and
provider events (§6.2, §11.2 financial scenario matrix).

## Decision
- **Topic registry**: `shared/pubsub/publisher.py`'s `Topic` enum is the
  single source of truth for every topic name; Terraform's
  `infra/terraform/main.tf` `locals.topics`/`locals.subscriptions` must
  mirror it 1:1 (a documented invariant that drifted twice this session —
  `arb-signals`, `arb-funding-rates-ledger` — both fixed; the discipline is
  now: any new `Topic` enum value requires a paired Terraform change in the
  same PR).
- **Envelope**: `shared/models/event_envelope.py`'s `EventEnvelope` is the
  canonical wrapper for business events that cross a service boundary
  (`event_id`, `event_type`, `occurred_at`, `producer`, `tenant_id`,
  `correlation_id`, `payload`) — used by `onramp`/`offramp-orchestrator`,
  `tenant-billing` (funding/payout/billing events), consumed by
  `settlement-status`/`webhook-service`. Proven live end-to-end this
  session (`scripts/settlement_flow_smoke.sh`) using the real
  `EventEnvelope.wrap()` call, not a hand-built fixture.
- **Idempotency (API)**: `shared/http/idempotency.py` (`InMemoryIdempotencyStore` /
  `RedisIdempotencyStore`) backs `Idempotency-Key` handling on write
  endpoints (`onramp`/`offramp-orchestrator` orders).
- **Idempotency (events)**: each Pub/Sub consumer either validates against a
  natural key (`Trade.id`, `MovementSignal.signal_id`) or, where a topic has
  no single canonical payload shape, against a canonical per-topic model —
  this was the actual bug fixed this session: `Topic.AUDIT_LOG` and
  `Topic.RISK_ALERTS` had NO canonical model, so their real producers sent
  shapes the consumer's `payload["field"]` indexing couldn't parse — every
  message crashed and nacked forever. Fixed by introducing
  `shared.models.AuditLogEntry` / `shared.models.RiskAlert` as the
  canonical per-topic contract, matching every other topic's existing
  pattern.

## Alternatives considered
- A generic "any topic accepts any dict" consumer contract — rejected:
  this is exactly what caused the audit-log/risk-alert bug; a topic
  without a canonical Pydantic model is not a contract, it's a hope.
- A full transactional outbox table per service (write-to-DB +
  poll-and-publish) — not yet implemented anywhere in this repo; current
  services publish directly and rely on fail-soft/fail-loud handling at
  the call site. Acceptable for the current volume/consistency
  requirements; flagged as a future hardening item, not a current gap.

## Consequences
- Every new topic MUST have a canonical model in `shared/models/` before a
  second producer or consumer is added — this is now a documented
  invariant (see ADR title), not just a convention.
- The lack of a true outbox pattern means a publish failure between a DB
  write and a Pub/Sub publish is possible in principle; every current
  publish call site is either fail-soft (advisory data, logged and
  swallowed) or fail-loud (audit path, raises) by deliberate design — never
  silently inconsistent.

## Revisit when
A service needs strict exactly-once delivery guarantees that fail-soft/
fail-loud handling can't provide (e.g. a real-money settlement path) — that
is the trigger for implementing a true outbox table.
