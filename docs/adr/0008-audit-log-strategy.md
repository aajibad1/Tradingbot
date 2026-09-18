# ADR-0008: Audit log strategy — one canonical event model, one sole BigQuery writer

Status: Accepted (fixed this session; previously broken in production)
Date: 2026-09-17
Owner: Security / Staff Engineer role

## Context
The contract requires every sensitive read/write to produce an audit record
(§8 Milestone 1 exit criteria) and every proposed agent action to be
recorded with model/prompt version and evidence (§3.K, docs/10). This repo
already had the plumbing (`Topic.AUDIT_LOG`, `trade-ledger` as sole BigQuery
writer) but it was **not actually working**.

## Decision
- `shared.models.AuditLogEntry` is the canonical, sole wire shape for
  `Topic.AUDIT_LOG` — `event_id, source, event_type, actor, action,
  resource_type, resource_id, metadata, emitted_at`, matching
  `services/trade-ledger/schema/audit_log.sql` field-for-field.
- `trade-ledger` is the sole writer to `arb_audit.audit_log` in BigQuery
  (unchanged — this was already correct); every producer must construct an
  `AuditLogEntry`, not publish its own domain object or a raw
  `EventEnvelope`, to that topic.
- Both current real producers (`ai-ops-agent` proposal audit mirror,
  `approval-gate-service` proposal-created/decided events) were found this
  session to be non-compliant — one published a raw `Proposal` object, the
  other an `EventEnvelope` — and every single audit-log message from
  either had been raising `KeyError` in the consumer and getting nacked
  forever. Fixed in both producers; regression tests added
  (`shared/tests/test_audit_log_entry.py`, updated tests in both
  producer services).
- The same defect class existed for `Topic.RISK_ALERTS` (kill-switch
  activations never reaching `arb_risk.risk_events`) — fixed identically
  with `shared.models.RiskAlert`, verified live via
  `scripts/paper_trade_local.sh`.

## Alternatives considered
- Let each producer publish its own shape and have the consumer branch on
  detected shape — rejected: this is what silently broke in the first
  place; a consumer that "handles anything" is a consumer that validates
  nothing.

## Consequences
- Before this fix, the actual audit trail for AI proposals and kill-switch
  events — arguably the two most safety/compliance-relevant event streams
  in the system — was silently empty in any real deployment. This was not
  caught by existing tests because each producer's unit tests only
  asserted "a publish call happened," never that the consumer could
  actually parse it.
- New rule for review (contract §10.4 applies directly): any PR that adds a
  new publisher to an existing topic must be reviewed against that topic's
  canonical model, not merely checked for "does it call `.publish()`."

## Revisit when
A third producer needs audit-log fields the current `AuditLogEntry` doesn't
support — extend the model (backward-compatibly, new optional fields),
never let a producer bypass it.
