# 05 Service Specs

## Purpose
This document defines the service-by-service implementation blueprint for the platform. It is intended to give Claude Code enough detail to scaffold, implement, and connect the production system predictably.

## Service inventory

### Public-facing services
- web-frontend
- developer-portal
- admin-console
- public-api-gateway

### Core platform services
- tenant-service
- auth-profile-service
- onboarding-service
- billing-service
- compliance-service
- notification-service
- audit-service
- feature-flag-service

### Shared-core intelligence and infrastructure services
- connector-runtime
- normalization-service
- route-optimizer
- venue-health-service
- fx-corridor-service
- wallet-registry-service
- event-router

### Trading plane services
- market-data-ingestor
- feature-builder
- signal-engine
- regime-engine
- opportunity-engine
- risk-engine
- paper-execution-engine
- live-execution-engine
- trade-ledger-service

### API plane services
- partner-auth-service
- api-metering-service
- tenant-billing-service
- onramp-orchestrator
- offramp-orchestrator
- settlement-status-service
- partner-webhook-service

### Agent services
- agent-orchestrator-service
- agent-memory-service
- agent-registry-service
- prompt-version-registry
- agent-evals-service
- approval-gate-service
- policy-enforcement-service
- research-session-service
- debate-session-service

## Standard service template
Each service should define:
- purpose
- main responsibilities
- API endpoints
- Pub/Sub topics consumed
- Pub/Sub topics emitted
- database tables touched
- auth/RBAC requirements
- failure modes
- observability metrics
- SLO expectations
- rollout dependencies

## Example service spec: onboarding-service

### Purpose
Owns onboarding flows for individual users, businesses, and partners.

### Responsibilities
- create onboarding sessions
- track KYC/KYB steps
- store onboarding states
- trigger compliance screening workflows
- emit onboarding lifecycle events

### Endpoints
- POST /onboarding/session
- GET /onboarding/session/:id
- POST /onboarding/session/:id/submit
- POST /onboarding/session/:id/review

### Consumes
- tenant.created
- user.created

### Emits
- onboarding.started
- onboarding.submitted
- onboarding.manual_review_requested
- onboarding.approved
- onboarding.rejected

### Data
- onboarding_sessions
- onboarding_documents
- onboarding_reviews

### Failure modes
- duplicate submissions
- provider timeout
- missing verification docs
- inconsistent tenant state

### Metrics
- onboarding started per day
- completion rate
- time to approval
- manual review rate
- provider failure rate

## Repo reconciliation (blueprint name → what's actually built)

The blueprint names above are aspirational; the repo already implements most of the
trading wedge under slightly different names. Status: ✅ built · 🟡 partial/stub · ❌ not started.

### Trading plane
| Blueprint | Repo service | Status |
|---|---|---|
| market-data-ingestor | `services/market-data` | ✅ |
| feature-builder | `services/movement-feature-builder` | ✅ |
| signal-engine | `services/signal-engine` | ✅ |
| regime-engine | `services/regime-classifier` | ✅ |
| opportunity-engine | `services/opportunity-engine` | ✅ |
| risk-engine | `services/risk-engine` (also the deterministic policy-enforcement) | ✅ |
| paper-execution-engine | `services/paper-trader` | ✅ |
| live-execution-engine | `services/execution-orchestrator` | 🟡 stub (Hummingbot client; no live orders) |
| trade-ledger-service | `services/trade-ledger` (sole BigQuery writer) | ✅ |

### Core platform
| Blueprint | Repo service | Status |
|---|---|---|
| tenant / auth-profile / onboarding / billing / compliance | `services/core-api` (consolidated control plane) | ✅ |
| notification-service | `services/notification-dispatcher` | ✅ |
| audit-service | core-api audit log + trade-ledger audit tables | ✅ |
| feature-flag-service | — | ❌ |

### Shared-core intelligence / infra
| Blueprint | Repo | Status |
|---|---|---|
| connector-runtime | `shared/connectors/adapter.py` (contract) | 🟡 contract only |
| normalization-service | `shared/utils/exchange_normalizer.py` (library, not a service) | 🟡 lib |
| route-optimizer | `services/route-optimizer` (advisory; calibrates from fills) | ✅ |
| venue-health-service | `services/venue-anomaly-detector` + market-data `health:latency:*` | ✅ |
| fx-corridor-service | `services/fx-rate-service` + `services/corridor-engine` | ✅ |
| wallet-registry-service | `services/account-link-service` (skeleton, superseded by managed custody) | 🟡 |
| event-router | Pub/Sub `Topic` enum + envelope (`shared/pubsub`, `shared/models/event_envelope.py`) | ✅ as infra |

### Public-facing
| Blueprint | Repo | Status |
|---|---|---|
| web-frontend | `apps/frontend` (Next.js) | ✅ |
| developer-portal / admin-console / public-api-gateway | — (`services/dashboard-api` is a partial ops API) | ❌ / 🟡 |

### API plane (live rails gated on licensing — see docs/REGULATORY_BRIEF.md)
| Blueprint | Repo | Status |
|---|---|---|
| onramp-orchestrator | `services/onramp-orchestrator` | 🟡 **SANDBOX only** (deterministic simulator; live rails gated) |
| offramp-orchestrator / settlement-status / partner-auth / api-metering / tenant-billing / partner-webhook | — | ❌ not started |

`onramp-orchestrator` is the doc-sanctioned sandbox-first build (docs/06 sandbox
tier): it exercises the full platform contract end to end — `install_contract`
(correlation + error model), Idempotency-Key on create, the `Status` taxonomy for
the order lifecycle, and `funding.*` events via the canonical envelope. No real
money moves; `sandbox_provider.assert_sandbox_only()` fails loud if pointed at a
real provider before licensing clears.

### Agent services
| Blueprint | Repo | Status |
|---|---|---|
| debate-session-service | `services/debate-service` (proposer→skeptic→judge) | ✅ |
| research-session-service | `services/corridor-intelligence-service`, `services/sentiment-service` (Perplexity) | 🟡 |
| policy-enforcement-service | `services/risk-engine` (deterministic gate, sole authority) | ✅ |
| approval-gate-service | partial in `services/ai-ops-agent` (propose→Slack-approve) | 🟡 |
| agent-orchestrator / agent-memory / agent-registry / prompt-version-registry / agent-evals | — | ❌ |

Plus repo services with no direct blueprint row: `opportunity-ranker` (advisory ranking),
`accounts-service` (internal double-entry ledger), `signal-replay-service` (replay tooling),
`status-service` (health/SLO aggregation), `ai-ops-agent` (Claude ops copilot).

## Required service sections for all services
Claude should expand this file so each service listed above gets a full implementation section using the same template.
