# ADR-0009: Payment approval — a separate segregation-of-duties service, not a field on approval-gate-service

Status: Accepted
Date: 2026-09-17
Owner: Staff Engineer role

## Context
Issue #21 requires that a payment request cannot reach a terminal
"submitted to provider" state without a recorded approval from an
authorized approver distinct from the requester (self-approval rejected),
with every decision — approved, rejected, or denied — audited.

This repo already has `services/approval-gate-service` (docs/10), which
classifies whether an **AI agent** may act autonomously: permission tiers
`auto` / `propose` / `blocked`, keyed on action type. It was tempting to
extend that service with a "requires N human approvals" tier instead of
building a new one.

## Decision
Build `services/payment-approval-service` as its own domain model, distinct
from `approval-gate-service`:

- **approval-gate-service** answers "may an AI agent perform this class of
  action at all" — a static permission classification keyed on action type,
  evaluated once per proposed action, with `withdrawals`/`leverage increases`
  hardcoded blocked regardless of caller.
- **payment-approval-service** answers "has this *specific* payment request
  been approved by a human distinct from whoever requested it" — a stateful,
  per-request workflow (`pending -> approved|rejected|expired`) with
  multi-approver quorum (`required_approvals`), an optional `authorized_approvers`
  allowlist, and unconditional self-approval denial.

These are orthogonal axes (action-class permission vs. per-instance human
sign-off) that happen to both gate money movement. Conflating them into one
service would mean either: (a) approval-gate-service grows per-request
mutable state it was never designed to hold (it's a stateless classifier
today), or (b) payment-approval-service inherits action-type permission
tiers it doesn't need (a payment approval request is always "requires
review" by construction — that's why one was created).

Wiring follows the same fail-closed, opt-in-per-order pattern established
by compliance-service (ADR-driven by issue #22): `onramp-orchestrator`
accepts an optional `approval_request_id` on order creation; `advance()`
gates `pending -> processing` on that request's status being `approved`,
via `PAYMENT_APPROVAL_SERVICE_URL`. Any other outcome — pending, rejected,
expired, unreachable, unset URL — routes to `awaiting_review`, never
provider submission. This gate is independent of (and composes with) the
existing `screening_check_id` compliance gate: an order may carry either,
both, or neither field, and every gate it carries must independently clear.

## Alternatives considered
- **Extend approval-gate-service with a "propose+quorum" tier** — rejected:
  would require adding request-scoped mutable state (votes, expiry,
  per-request approver lists) to a service designed around a fixed,
  static permission table; the two lifecycles (agent-permission-checked-once
  vs. payment-approval-workflow-with-state) don't share a natural
  persistence model.
- **Model approval as a field inside onramp-orchestrator itself** — rejected:
  the same approval primitive is needed by offramp-orchestrator and future
  payment-adjacent services (issue #21 scope is payments generically, not
  onramp specifically); a shared service avoids duplicating the
  segregation-of-duties state machine per consumer, exactly as
  compliance-service is shared rather than embedded per-orchestrator.

## Consequences
- Two services now both gate money movement for different reasons; a
  future reader must check both `approval-gate-service` (can an AI agent
  even propose this) and `payment-approval-service` (has a human, other
  than the requester, actually signed off) rather than assuming one gate
  covers both concerns.
- `onramp-orchestrator.advance()` now runs up to two independent external
  HTTP calls per gated status transition (screening + approval); both fail
  closed, so an outage of either service holds orders at
  `awaiting_review` rather than blocking startup or crashing the request.
- offramp-orchestrator is not yet wired to either gate — tracked as a
  follow-up (mirrors the same gap left after issue #22 for offramp).

## Revisit when
A payment type needs approval routing beyond flat quorum (e.g. amount-tiered
approval counts, role-based approver sets) — extend
`payment-approval-service`'s request-creation contract rather than adding a
second approval service.
