# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

Monorepo of independent Python 3.12 microservices that talk over GCP Pub/Sub. Each service in `services/<name>/` is self-contained (its own `requirements.txt` and `Dockerfile`) and depends only on `shared/` and Pub/Sub messages — never on another service's code.

```
services/
  # Execution plane — Global market (crypto), paper mode
  market-data/             CCXT Pro WebSocket collectors → arb-market-data
  funding-rate-service/    60s polling of CoinGlass / ArbitrageScanner / CCXT → arb-funding-rates
  opportunity-engine/      Subscribes to ticks + funding, runs strategies → arb-opportunities
  risk-engine/             Choke point. Gates + forwards APPROVED opps (execute=True) → arb-approved;
                           owns Redis risk:* keys, kill switch, reads eligibility:t:* + advisory rank
  paper-trader/            Simulates arb-approved opportunities → arb-trade-fills
  execution-orchestrator/  Hummingbot client for live orders (consumes arb-approved; stubbed gateway)
  trade-ledger/            Sole BigQuery writer; tax-export endpoint
  opportunity-ranker/      AI advisory ranking (never gates; risk-engine calls it fail-open)
  # Execution plane — Africa market (FX/stablecoin corridors), alert-only
  corridor-engine/         Scores corridors w/ settlement risk → arb-corridor-alerts
  # API plane — partner orchestration (docs/05–06). SANDBOX only; live rails gated on licensing.
  public-api-gateway/      Front door: authenticate (partner-auth) + meter (api-metering) + proxy
  developer-portal/        Self-serve key mgmt + sandbox API playground + catalog (FastAPI-served UI)
  admin-console/           Ops/compliance oversight UI: health + per-tenant keys/usage/invoices/webhooks
  routing-service/         Resolve best provider/corridor (cost|speed) for a transfer; advisory
  wallet-service/          Per-tenant wallets + aggregated balances; overdraft-protected adjust
  onramp-orchestrator/     Fiat→stablecoin on-ramp (sandbox simulator) → funding-events
  offramp-orchestrator/    Stablecoin→fiat payout (sandbox simulator) → payout-events
  settlement-status/       Read model: projects funding+payout events → normalized settlement
  webhook-service/         HMAC-signed partner webhook delivery (funding/payout); rotate + replay
  partner-auth/            API-key issue/verify/rotate/revoke (secrets hashed); prod keys gated
  api-metering/            Per-tenant API usage by route/period; quotas + soft overage
  tenant-billing/          Usage→invoices (plan base + overage); issue/pay → billing-events
  # Control plane — multi-tenant SaaS (Cloud SQL Postgres)
  core-api/                Identity/tenant/RBAC, onboarding state machine, Stripe billing→entitlements,
                           live-enable gate (perm×plan×onboarding×funding), audit, funding, dashboard,
                           Clerk webhook sync. Publishes eligibility:t:* for risk-engine.
  accounts-service/        Internal double-entry ledger (per-tenant balances), paper mode
  account-link-service/    Non-custodial key-link skeleton (SUPERSEDED by managed custody)
  # Platform services
  sentiment-service/  notification-dispatcher/  fx-rate-service/  dashboard-api/  ai-ops-agent/
  approval-gate-service/   AI permission-model gate (read→auto, sensitive→human, withdrawals→blocked); audit (docs/10)
  agent-registry/          Agents + immutable prompt/model versions; activation gated on passing eval (docs/10)
  agent-evals/             Eval harness (accuracy/hallucination/latency) + promotion verdicts (docs/10)
  connector-runtime/       Shared-core: registry of pluggable venues + health read model (docs/03; no creds/IO)
apps/
  frontend/                Next.js (App Router): onboarding stepper + dashboard → core-api
shared/
  models/                  Pydantic schemas shared across every service (Opportunity, Trade, RiskState, ...)
  models/event_envelope.py Canonical event envelope (docs/07): EventEnvelope.wrap()/.attributes()
  pubsub/publisher.py      Topic enum + EventPublisher/NullPublisher factory; publish_event() wraps in envelope
  http/                    API contract (docs/06): install_contract(app) = correlation (x-request-id/
                           x-correlation-id) + standard error model (APIError) + idempotency store
  tenant.py                Tenant namespacing: risk_key, exchange_secret_id, eligibility_key
  utils/                   fee_calculator, exchange_normalizer, slippage_model
# Blueprint layer — numbered handoff (01–12); each ends with an "Expansion task"
docs/01-master-strategy-architecture.md  Vision, two monetization layers, build order
docs/03-architecture.md                  Layered technical architecture (dual-business + agents)
docs/05-service-specs.md                 Service-by-service implementation blueprint
docs/06-api-contracts.md                 External/internal API contracts, correlation, errors
docs/07-data-event-schema.md             Canonical entities + event taxonomy + envelope (impl: shared/models/event_envelope.py)
docs/09-security-compliance.md           Security/compliance controls + go-live gates
docs/10-agent-governance.md              Multi-agent safety, versioning, evals, approval gating
docs/11-runbooks-go-live.md              Runbooks + go-live checklist
# Reference layer — canonical detail the code points at (load-bearing)
docs/MASTER_STRATEGY.md          Strategy index + repo coverage map (reconciles briefs to repo)
docs/ARCHITECTURE.md             Data-flow, service ownership, risk-control invariants
docs/PLATFORM_ARCHITECTURE.md    Multi-tenant two-market target + repo reconciliation
docs/REGULATORY_BRIEF.md         Custody/licensing brief for counsel
docs/CONNECTOR_ARCHITECTURE.md   Pluggable connector contract
docs/STRATEGY_GUIDE.md           Strategies + net-edge math   docs/RISK_POLICY.md  Limits/kill switch/drawdown
docs/EXCHANGE_FEES.md            Canonical per-venue taker fees   docs/TAX_COMPLIANCE.md  Wash-sale/FIFO/HIFO
docs/MULTI_TENANCY.md  Tenant namespacing   docs/TRADING_ASSUMPTIONS.md  Promotion gates   docs/SLOS.md  SLO targets
```
Two doc layers: the **blueprint** set (01–12) is the forward-looking handoff;
the **reference** docs carry canonical detail (fees, risk limits, tax, SLOs) that
code comments point at by name. Keep both in sync when either changes.

Two markets, one core: **Global** = crypto funding-carry/basis (execution loop, paper);
**Africa** = FX/stablecoin corridor arb (alert-only). See `docs/01-master-strategy-architecture.md` + `docs/03-architecture.md`.
The control plane (`core-api`/`accounts-service`) is **Cloud SQL Postgres-backed** and runs
locally on SQLite with no cloud deps (`AUTH_PROVIDER` unset = local bearer; managed custody
decided — see `docs/09-security-compliance.md`).

Run the whole thing locally (no GCP): `./scripts/local_stack.sh` boots the wedge +
control plane + analytics together on SQLite/Redis/NullPublisher and stays up for
interactive testing (status-service at :8087 aggregates the live mesh: `/status`,
`/slo/catalog`, `/slo/evaluate`). Needs a reachable Redis.
Run the SANDBOX API plane locally (no GCP/Redis): `./scripts/api_plane_stack.sh`
boots all 13 API-plane services wired together + the Developer Portal (:8322) and
Admin Console (:8323), and stays up (`SMOKE=1` to boot-check + exit).

End-to-end local smokes (no GCP):
`./scripts/control_plane_smoke.sh` (onboarding→billing→funding→live-enable→dashboard),
`./scripts/paper_trade_local.sh` (risk-engine + paper-trader trade flow),
`./scripts/failover_smoke.sh` (status-service ok→degraded→down semantics under
killed dependencies — reliability #1), and
`./scripts/api_plane_smoke.sh` (sandbox API plane: partner-auth→gateway→{routing,
onramp,wallet}; auth→scope→meter→proxy). All use only indexed bash arrays (macOS bash 3.2).
Frontend: `cd apps/frontend && npm install && npm run dev` (needs core-api on :8080).

## Running tests

There is no top-level package install or `pytest.ini`. Each service is run with its own directory and the repo root added to `PYTHONPATH` — the Dockerfile pattern (`COPY shared ./shared; COPY services/<svc>/ ./; ENV PYTHONPATH=/app`) is what makes both `from shared.models...` and `from rules.kill_switch...`-style intra-service imports resolve. Replicate that locally:

```bash
# Single service
PYTHONPATH=.:services/risk-engine          python3 -m pytest services/risk-engine/tests/ -v
PYTHONPATH=.:services/paper-trader         python3 -m pytest services/paper-trader/tests/ -v
PYTHONPATH=.:services/market-data          python3 -m pytest services/market-data/tests/ -v
PYTHONPATH=.:services/funding-rate-service python3 -m pytest services/funding-rate-service/tests/ -v
PYTHONPATH=.:services/opportunity-engine   python3 -m pytest services/opportunity-engine/tests/ -v

# Control-plane + new services (SQLite-backed; no cloud deps)
PYTHONPATH=.:services/core-api             python3 -m pytest services/core-api/tests/ -v
PYTHONPATH=.:services/accounts-service     python3 -m pytest services/accounts-service/tests/ -v
PYTHONPATH=.:services/corridor-engine      python3 -m pytest services/corridor-engine/tests/ -v
PYTHONPATH=.:services/opportunity-ranker   python3 -m pytest services/opportunity-ranker/tests/ -v
PYTHONPATH=.:services/onramp-orchestrator  python3 -m pytest services/onramp-orchestrator/tests/ -v
PYTHONPATH=.:services/offramp-orchestrator python3 -m pytest services/offramp-orchestrator/tests/ -v
PYTHONPATH=.:services/settlement-status    python3 -m pytest services/settlement-status/tests/ -v
PYTHONPATH=.:services/webhook-service      python3 -m pytest services/webhook-service/tests/ -v
PYTHONPATH=.:services/partner-auth         python3 -m pytest services/partner-auth/tests/ -v
PYTHONPATH=.:services/api-metering         python3 -m pytest services/api-metering/tests/ -v
PYTHONPATH=.:services/tenant-billing       python3 -m pytest services/tenant-billing/tests/ -v
PYTHONPATH=.:services/routing-service      python3 -m pytest services/routing-service/tests/ -v
PYTHONPATH=.:services/wallet-service       python3 -m pytest services/wallet-service/tests/ -v
PYTHONPATH=.:services/public-api-gateway   python3 -m pytest services/public-api-gateway/tests/ -v
PYTHONPATH=.:services/developer-portal     python3 -m pytest services/developer-portal/tests/ -v
PYTHONPATH=.:services/admin-console        python3 -m pytest services/admin-console/tests/ -v
PYTHONPATH=.:services/approval-gate-service python3 -m pytest services/approval-gate-service/tests/ -v
PYTHONPATH=.:services/agent-registry       python3 -m pytest services/agent-registry/tests/ -v
PYTHONPATH=.:services/agent-evals          python3 -m pytest services/agent-evals/tests/ -v
PYTHONPATH=.:services/connector-runtime    python3 -m pytest services/connector-runtime/tests/ -v

# All services in one run (note PYTHONPATH order)
PYTHONPATH=.:services/risk-engine:services/paper-trader:services/market-data:services/funding-rate-service \
  python3 -m pytest services/ -q

# Single test
PYTHONPATH=.:services/risk-engine python3 -m pytest services/risk-engine/tests/test_position_limits.py::test_rejects_low_net_edge -v
```

There is no Makefile or shared `pyproject.toml`. Dependencies are pinned per service in `services/<name>/requirements.txt` — install only what the service you're touching needs.

## Running a service locally

Each service is a FastAPI app started with uvicorn:

```bash
cd services/<name> && PYTHONPATH=../..:. uvicorn main:app --port 8080
```

The system is designed to run without GCP credentials locally:

- `shared/pubsub/publisher.py#get_publisher()` returns `NullPublisher` (logs to stdout instead of publishing) whenever `GCP_PROJECT_ID` is unset.
- `services/market-data/secret_loader.py#get_secret` falls back to `LOCAL_SECRET_<UPPER_SNAKE_ID>` env vars when `GCP_PROJECT_ID` is unset.
- `opportunity-engine/main.py` skips Pub/Sub subscriber wiring when `GCP_PROJECT_ID` is unset; call `POST /evaluate-now` to drive a manual evaluation cycle.
- `trade-ledger/main.py` skips subscriber wiring when `GCP_PROJECT_ID` is unset — useful for hitting `/tax-export` in isolation.

`risk-engine` is the exception: it always requires Redis (`REDIS_URL`, defaults to `redis://localhost:6379/0`) and `risk:capital_usd` must be set in Redis before `/evaluate` will work — `load_state` fails loud if capital is unset.

## Architecture you must respect

### Pub/Sub topics are the contract

Every cross-service interaction is a Pub/Sub message. The full topic list lives in **one place**: the `Topic` enum in `shared/pubsub/publisher.py`. Never hardcode a topic name at a call site; add or use an enum value. The matching wire schemas are the Pydantic models in `shared/models/` — changing one of those models can break every consumer at once.

Subscriptions follow the convention `<topic-name>-<consumer-service>` (e.g. `arb-market-data-opp-engine`, `arb-trade-fills-ledger`). Adding a new consumer means creating a new subscription on the existing topic, not a new topic.

### Single source of truth for net-edge math

`shared/utils/fee_calculator.py#calculate_net_edge` is the only place the spread/fee/slippage/buffer formula is allowed to live. The `opportunity-engine` scorer calls it; strategies must **not** reimplement it. `MIN_VIABLE_NET_EDGE_BPS = 50.0` and `DEFAULT_SAFETY_BUFFER_BPS = 3.0` are the canonical thresholds (the 50 bps bar was raised from an earlier 8 bps after real-data backtests showed modeled edge running 1.5–10× realized — see the module docstring and `scripts/validate_strategy.py`). Per-exchange taker fees live in `EXCHANGE_TAKER_FEE_BPS` in the same module — `taker_fee_bps()` raises `KeyError` on an unknown exchange (intentional: fail loud, never silently default).

### risk-engine is the choke point

Every opportunity must be approved by `risk-engine` before it can be executed (paper or live). Order of checks in `services/risk-engine/main.py#evaluate`:

1. Kill switch — short-circuits, no other rules even run.
2. Position limits (size, exchange exposure, asset concentration, leverage, min net edge).
3. Drawdown — **a breach trips the kill switch synchronously inside `evaluate`**, so the same request both reports the violation and locks down the system.
4. Exchange health (latency).

`DEFAULT_LIMITS` in `services/risk-engine/rules/drawdown_guard.py` is the canonical limit table. Resetting the kill switch requires `KILL_SWITCH_RESET_TOKEN` — there is no UI bypass.

### Redis is the shared risk state

The `risk:*` keys in Redis are documented at the top of `services/risk-engine/state.py`. **`risk-engine` is the sole writer** of those keys; `market-data` writes only its own `health:latency:<exchange>` keys (consumed by `risk-engine` for exchange-health checks). Anything else is read-only against Redis.

### paper-trader does not write to BigQuery

`paper-trader` publishes simulated Trades to `arb-trade-fills`; `trade-ledger` is the **only** service that writes to BigQuery. Keep that boundary — adding a direct BQ write anywhere else breaks the auditable single-write-path invariant called out in `services/paper-trader/ledger.py`.

### opportunity-engine hot path

`services/opportunity-engine/main.py` uses an in-memory `MarketSnapshot` updated by Pub/Sub callbacks. Strategies are throttled to one evaluation per `EVAL_INTERVAL_S` (default 1.0s) — do not call strategies on every tick, you'll get thousands of evals/sec. Above-threshold opportunities are published freely; `risk-engine` is the filter.

## Conventions worth knowing

- **Symbol normalization**: cross-venue comparisons use the canonical form from `shared/utils/exchange_normalizer.py` (`BTC/USD` or `BTC/USD:PERP`). `USDT`/`USDC`/`ZUSD` all collapse to `USD`. Kraken's `XBT` → `BTC`.
- **Funding period**: Hyperliquid funds hourly (1.0h); other CEX perps fund every 8h. The split lives in `services/funding-rate-service/normalizer.py#from_ccxt` and `services/paper-trader/simulator/funding_simulator.py#funding_period_hours`.
- **AI permission model** (see `docs/10-agent-governance.md`): read-only ops are autonomous; risk-limit or trade-size changes are propose-only and require Slack approval; withdrawals / leverage increases are hardcoded blocked.
