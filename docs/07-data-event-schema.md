# 07 Data and Event Schema

## Purpose
Defines the canonical data model and event taxonomy for the platform.

## Core entities
- users
- tenants
- memberships
- subscriptions
- api_keys
- webhook_endpoints
- onboarding_sessions
- compliance_cases
- connectors
- venues
- corridors
- routes
- balances
- orders
- fills
- opportunities
- signals
- settlements
- funding_requests
- payout_requests
- audit_entries
- agent_sessions
- debate_sessions

## Event taxonomy
- auth.*
- tenant.*
- onboarding.*
- compliance.*
- billing.*
- market.tick.*
- market.orderbook.*
- signal.*
- regime.*
- opportunity.*
- risk.*
- trade.*
- funding.*
- payout.*
- settlement.*
- webhook.*
- audit.*
- agent.*
- debate.*

## Event envelope
```json
{
  "event_id": "evt_123",
  "event_type": "opportunity.created",
  "occurred_at": "2026-06-09T22:00:00Z",
  "producer": "opportunity-engine",
  "tenant_id": "ten_123",
  "correlation_id": "corr_123",
  "version": 1,
  "payload": {}
}
```

**Implemented:** this envelope is `shared/models/event_envelope.py#EventEnvelope`
(`.wrap()` + `.attributes()`), published via
`shared/pubsub/publisher.py#publish_event()`. It is **additive and opt-in**: new
event flows emit wrapped envelopes (full envelope as message data, metadata
mirrored to Pub/Sub attributes for filtering); legacy topics keep their raw
Pydantic-payload format and migrate one at a time, so no consumer breaks at once.
The `correlation_id` is the same thread used by the API correlation model (docs/06).

## Data rules
- canonical IDs across services
- immutable event history
- append-only audit trail for sensitive actions
- PII tagging and isolation
- soft delete where required
- retention schedule by data class

## Schema expansion
Claude should convert these into concrete database tables, indexes, topic definitions, and JSON schemas.
