# Service-level objectives (SLOs)

Trust/reliability is the #1 product priority (docs/MASTER_STRATEGY.md). These are
the targets the status-service + monitoring measure against. Numbers are launch
targets, to be tightened with real traffic.

## Platform
| SLO | Target | Measured by |
|---|---|---|
| Control-plane API availability (core-api, dashboard-api) | 99.9% | status-service /status (200 vs 503) |
| Status-page accuracy | reflects reality within 30s | probe cadence |

## Execution plane
| SLO | Target | Measured by |
|---|---|---|
| Market-data freshness (health:latency:* in Redis) | < 30s staleness | risk-engine exchange-health gate |
| Event processing lag (opportunity → arb-approved) | p95 < 2s | trade-ledger / BigQuery |
| Webhook idempotency correctness (Stripe/Clerk) | 100% (deduped) | webhook_events table |
| Paper-trade execution acceptance rate | tracked, no hard target | trade-ledger |

## Reliability behaviors (enforced in code)
- Connector reconnect with exponential backoff; one bad symbol never takes a venue down.
- Idempotent webhook + event-consumer write paths (provider event id / opportunity id).
- Graceful degradation: AI advisory fail-open; accounts/funding optional-client; baseline fallbacks.
  - *Tested failover:* the choke point survives a dead advisory ranker — a
    configured-but-down opportunity-ranker never blocks approval
    (`services/risk-engine/tests/test_advisory_failopen.py`).
- Kill switch + drawdown guard cap downside synchronously.

## Status semantics
status-service `/status` aggregates dependency probes:
- **ok** (HTTP 200) — all critical components healthy.
- **degraded** (HTTP 200) — a non-critical component is down (platform still
  serves, so external uptime monitors must NOT trip).
- **down** (HTTP 503) — a critical component is down; external uptime monitors trip.

*Tested failover:* `scripts/failover_smoke.sh` boots the live mesh and kills a
non-critical then a critical dependency, asserting the ok(200) → degraded(200) →
down(503) transitions hold against real services.
