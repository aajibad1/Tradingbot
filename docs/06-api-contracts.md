# 06 API Contracts

## Purpose
Defines the external and internal API contracts, webhook payloads, status taxonomy, idempotency rules, and request correlation model.

## Standards
- JSON over HTTPS
- explicit versioning: /v1
- idempotency key required for write operations
- x-request-id and x-correlation-id on every request
- RFC3339 timestamps
- deterministic status codes

The `x-correlation-id` here is the same `correlation_id` carried by the event
envelope (docs/07, `shared/models/event_envelope.py`), so a single business
action is traceable across the API edge and the event bus.

## Core external APIs

### Auth and tenant
- POST /v1/auth/session
- GET /v1/tenants/me
- POST /v1/tenants

### Wallet and balances
- GET /v1/wallets
- GET /v1/wallets/{id}
- GET /v1/balances

### On-ramp
- POST /v1/onramp/quotes
- POST /v1/onramp/orders
- GET /v1/onramp/orders/{id}

### Off-ramp
- POST /v1/offramp/quotes
- POST /v1/offramp/orders
- GET /v1/offramp/orders/{id}

### Routing
- POST /v1/routes/resolve
- GET /v1/routes/{id}

### Webhooks
- POST /v1/webhooks/endpoints
- GET /v1/webhooks/endpoints
- POST /v1/webhooks/endpoints/{id}/rotate-secret
- POST /v1/webhooks/endpoints/{id}/replay

## Webhook event types
- funding.created
- funding.processing
- funding.completed
- funding.failed
- payout.created
- payout.processing
- payout.completed
- payout.failed
- settlement.updated
- compliance.review_requested
- compliance.approved
- compliance.rejected
- webhook.delivery.failed

## Webhook payload standard
```json
{
  "id": "evt_123",
  "type": "payout.completed",
  "created_at": "2026-06-09T22:00:00Z",
  "tenant_id": "ten_123",
  "correlation_id": "corr_123",
  "data": {}
}
```

## Error model
```json
{
  "error": {
    "code": "route_unavailable",
    "message": "No route available for the requested corridor",
    "request_id": "req_123",
    "correlation_id": "corr_123"
  }
}
```

## Status taxonomy
- pending
- queued
- processing
- awaiting_review
- completed
- partially_completed
- failed
- cancelled

## Implemented (shared layer)
The cross-cutting parts of this contract are implemented in `shared/http/`:
- correlation (`x-request-id` / `x-correlation-id` propagate + echo) — `shared/http/correlation.py`
- standard error model + status taxonomy — `shared/http/errors.py` (`APIError`, `Status`)
- idempotency-key store for write ops — `shared/http/idempotency.py`
- one-line wiring — `from shared.http import install_contract; install_contract(app, service_name=...)`

Adopted so far: `status-service`. New API-plane services should `install_contract`
on startup. The per-endpoint OpenAPI definitions below remain to be expanded.

## Contract requirements
Claude should expand this into full OpenAPI-ready endpoint definitions with request/response examples, error cases, and webhook examples.
