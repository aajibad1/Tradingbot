# Claude Code Starter Document — Crypto Arbitrage SaaS on GCP for Africa and Global Markets (Clerk Version)

## Purpose

This document is a build-ready technical brief for Claude Code to begin implementation of a crypto arbitrage SaaS platform on Google Cloud Platform using Clerk for authentication. The target platform serves both African and global markets using a shared event-driven core, with regional policy and connector packs layered on top.[cite:84][cite:85][cite:48]

The product should be designed as a managed SaaS platform rather than a bring-your-own-API-key bot. End users sign up, complete onboarding, subscribe, fund accounts through supported rails, and use platform-managed trading workflows without needing to understand exchange APIs.[cite:67][cite:70]

## Product model

The product has three operating planes:

- Control plane: user authentication, onboarding, subscriptions, RBAC, account status, disclosures, and auditability.[cite:178][cite:186]
- Execution plane: venue connectors, streaming market data, normalization, opportunity scoring, risk checks, and trade orchestration.[cite:84][cite:85][cite:107]
- Intelligence plane: replay, analytics, route-quality scoring, PnL attribution, cohort analytics, and later ranking models.[cite:86][cite:88]

The system must support two market modes on one platform:

- Africa mode: stablecoin rails, corridor and FX intelligence, country policy routing, mobile-first onboarding, local payout logic.[cite:48][cite:51][cite:150]
- Global mode: deeper exchange connectivity, high-quality order books, broader venue scoring, and more execution-centric arbitrage logic.[cite:131][cite:135][cite:167]

## Core assumptions

- Users do not bring exchange API keys and should never be asked to understand APIs in the product UI.[cite:67][cite:70]
- The platform owns the exchange, rail, and on-chain integrations through backend-managed credentials stored securely in Secret Manager.[cite:62][cite:68][cite:121]
- Authentication and authorization are separate concerns: Clerk handles identity and sessions, while platform RBAC in Postgres determines permissions and entitlements.[cite:176][cite:181][cite:186]
- The architecture should be event-driven and GCP-native, using Pub/Sub and Cloud Run as the primary application backbone.[cite:84][cite:85][cite:107]
- The initial system should optimize for strong SaaS-grade latency and reliability, not high-frequency trading microsecond infrastructure.[cite:26][cite:137]

## Recommended stack

| Layer | Tooling | Notes |
|---|---|---|
| Frontend | Next.js or React | Multi-step onboarding, dashboard, admin UI |
| End-user auth | Clerk | Sign-in, sign-up, MFA, sessions, user management, organizations.[cite:188][cite:186] |
| Authorization | Cloud SQL Postgres RBAC tables | Roles, entitlements, tenant permissions.[cite:176][cite:178] |
| Backend APIs | Cloud Run + FastAPI or NestJS | Primary app and internal APIs.[cite:23][cite:107] |
| Event bus | Pub/Sub | Service-to-service async events.[cite:85][cite:107] |
| Secrets | Secret Manager | Platform-side credentials and keys.[cite:62][cite:121] |
| Hot cache | Memorystore Redis | Best bid/ask, live books, venue health, session state.[cite:115][cite:129] |
| Relational DB | Cloud SQL Postgres | Users, tenants, onboarding, orders, balances, audits.[cite:119] |
| Analytics warehouse | BigQuery | Replay, PnL, route scoring, reporting.[cite:137] |
| Stream processing | Cloud Run initially, Dataflow later if needed | Start simple, expand only if transforms become heavy.[cite:84][cite:86] |
| Billing | Stripe Billing | Subscriptions, upgrades, invoices, entitlements.[cite:37][cite:114] |
| Webhooks | Cloud Run webhook service | Stripe billing events and Clerk user lifecycle sync.[cite:178][cite:179][cite:114] |
| Monitoring | Cloud Logging, Monitoring, Error Reporting | Production observability.[cite:88] |

## Auth and authorization design

### Authentication

Use Clerk for end-user sign-up, sign-in, MFA, session management, and organization-aware account UX. The frontend receives a Clerk session and sends the session token to backend APIs. Cloud Run services verify Clerk JWTs using Clerk's manual JWT verification or JWKS-based flow before processing user requests.[cite:181][cite:187][cite:188]

### Authorization

Do not store product authorization logic only in Clerk. Maintain application-level authorization in Postgres with the following core tables:

- `users`
- `tenants`
- `tenant_memberships`
- `roles`
- `permissions`
- `plan_entitlements`
- `trading_account_permissions`
- `kyc_status`
- `onboarding_status`

Suggested initial roles:

- `owner`
- `operator`
- `analyst`
- `support`
- `admin`

Trading permissions should be explicit and granular:

- `view_portfolio`
- `view_opportunities`
- `start_paper_trading`
- `enable_live_trading`
- `request_withdrawal`
- `manage_team`
- `view_audit_log`
- `manage_risk_limits`

### Clerk sync model

Clerk should be treated as the identity source, not the full system of record. Sync user lifecycle events into Postgres using Clerk webhooks so internal services can join user identities with tenants, onboarding state, billing, trading accounts, and audits.[cite:178][cite:179][cite:182]

Recommended webhook events to mirror:

- `user.created`
- `user.updated`
- `user.deleted`
- `organization.created`
- `organization.updated`
- `organizationMembership.created`
- `organizationMembership.updated`

Use idempotent webhook processing and signature verification for all Clerk events.[cite:179][cite:182]

## User onboarding flow

The onboarding flow should feel like a fintech app, not a developer console. Use one guided stepper and hide all API complexity.[cite:67][cite:70]

Recommended onboarding steps:

1. Create account with Clerk: email, phone, passkey, or social sign-in depending on enabled factors.[cite:188]
2. Verify identity factors and enable MFA for sensitive operations.[cite:188]
3. Choose region: Africa or global market path.
4. Collect personal or business profile.
5. Run KYC/KYB and sanctions checks before enabling trading.[cite:61][cite:64][cite:70]
6. Select plan and pay through Stripe Billing.[cite:37][cite:114]
7. Capture disclosures and risk acknowledgment.[cite:69]
8. Select funding method and permitted payout route.[cite:64][cite:150]
9. Mark account as `trading_ready`, `pending_review`, or `restricted`.

Suggested onboarding states:

- `account_created`
- `identity_pending`
- `identity_verified`
- `billing_pending`
- `billing_active`
- `funding_pending`
- `review_pending`
- `trading_ready`
- `restricted`

## Trading model

The platform is a managed arbitrage SaaS. The user interacts only with the product UI. The backend handles venue connectivity, order submission, and portfolio updates.[cite:70][cite:82]

Core execution flow:

1. Feed collectors stream market data from exchanges, brokers, and on-chain sources.[cite:131][cite:136][cite:137]
2. Normalizers convert venue-specific payloads into one internal event schema.[cite:138][cite:149]
3. Redis stores hot order-book and top-of-book state.[cite:129]
4. Opportunity engine calculates spreads and net opportunity values after cost assumptions.[cite:74][cite:77]
5. Risk engine checks balances, user permissions, policy constraints, and kill-switch state.[cite:78][cite:7]
6. Execution orchestrator routes paper or live orders and records outcomes.[cite:75][cite:79]
7. Ledger and analytics layers capture the complete result trail.[cite:78][cite:137]

## Connector strategy

Use a tiered connector model. CCXT Pro is the best breadth layer for centralized exchange market streams, while on-chain venues require separate providers such as Alchemy or Bitquery.[cite:131][cite:133][cite:136]

### Centralized exchange breadth layer

- Use `ccxt.pro` with Python `asyncio` for initial multi-exchange WebSocket collection.[cite:131][cite:133][cite:135]
- Normalize all incoming updates into an internal schema, not exchange-native schemas.[cite:138]
- For top-priority venues, plan to add native adapters later if sequence handling, latency, or feature depth becomes important.[cite:138][cite:141]

### Africa launch connectors

| Connector | Type | Why it matters |
|---|---|---|
| Yellow Card | Pan-African stablecoin rail / exchange access | Strong local-currency and stablecoin relevance across African markets.[cite:168][cite:171] |
| VALR | South Africa exchange | Strong REST and WebSocket support for market and order updates.[cite:159][cite:160] |
| Luno | Africa / emerging markets exchange | Developer API with trading, account, and market data support.[cite:166] |
| FX provider | FX market data | Required for corridor pricing and net-profit scoring in NGN, ZAR, KES, GHS and related corridors.[cite:150][cite:153][cite:172] |

Suggested African priority countries:

- Nigeria
- South Africa
- Kenya
- Ghana

These markets are repeatedly identified as high-activity or strategically important African crypto markets and are moving toward more structured digital asset regulation.[cite:148][cite:40][cite:44]

### Global launch connectors

| Connector | Type | Why it matters |
|---|---|---|
| CCXT Pro | Multi-exchange SDK | Unified WebSocket support across many exchanges.[cite:131][cite:133] |
| Coinbase Advanced Trade | Centralized exchange | Strong WebSocket market data and user order channels.[cite:167] |
| Binance-compatible adapter | Centralized exchange | Strong public market data and deep order-book coverage.[cite:170][cite:173] |
| Kraken or similar tier-1 venue | Centralized exchange | Adds liquidity diversity and execution redundancy.[cite:158] |
| Alchemy | On-chain node/data provider | Best for smart contract events, balances, and chain-native observability.[cite:136] |
| Bitquery | On-chain data provider | Useful for DEX and token analytics across chains.[cite:136] |
| CoinGecko-style aggregator | Reference data | Only for metadata and validation, not primary execution.[cite:136] |

## Internal event schema

All connectors should emit a normalized event schema to Pub/Sub. Start with these event families:

- `market.ticker.updated`
- `market.orderbook.snapshot`
- `market.orderbook.delta`
- `market.trade.printed`
- `market.venue.health`
- `fx.rate.updated`
- `wallet.balance.updated`
- `opportunity.detected`
- `opportunity.approved`
- `opportunity.rejected`
- `trade.submitted`
- `trade.executed`
- `trade.failed`
- `billing.updated`
- `auth.user.synced`
- `auth.organization.synced`
- `onboarding.submitted`
- `onboarding.state.changed`
- `risk.limit.triggered`
- `kill_switch.activated`

Suggested canonical fields for market events:

```json
{
  "event_id": "uuid",
  "event_type": "market.orderbook.delta",
  "timestamp": "ISO-8601",
  "source": "valr",
  "region": "africa",
  "market_type": "cex",
  "symbol": "BTC/USDT",
  "base_asset": "BTC",
  "quote_asset": "USDT",
  "bids": [["price", "size"]],
  "asks": [["price", "size"]],
  "sequence": "string-or-int",
  "raw": {}
}
```

## Core services to implement

### Public services

- `frontend-web`
- `core-api`
- `dashboard-api`
- `billing-webhook`
- `clerk-webhook-sync`

### Internal services

- `onboarding-service`
- `connector-ccxt-service`
- `connector-africa-rails-service`
- `connector-onchain-service`
- `fx-feed-service`
- `normalizer-service`
- `opportunity-engine`
- `risk-engine`
- `execution-orchestrator`
- `ledger-service`
- `alerting-service`
- `analytics-exporter`

## GCP deployment design

Use Cloud Run as the default execution environment for all stateless services. Cloud Run supports WebSockets and private networking to internal resources. Use Direct VPC egress for Redis and private Cloud SQL access when possible.[cite:140][cite:122][cite:129]

### GCP resources

- Cloud Run services for frontend APIs, feed collectors, normalizers, risk, execution, webhooks, and dashboards.[cite:107][cite:122]
- Pub/Sub topics and subscriptions as the async event backbone.[cite:85]
- Cloud SQL Postgres for users, tenants, entitlements, orders, audits, and operational records.[cite:119]
- Memorystore Redis for hot market state and live UI state.[cite:115][cite:129]
- BigQuery for historical facts, replay, and analytics.[cite:137]
- Secret Manager for venue credentials, Clerk verification materials if needed, webhook verification secrets, and internal keys.[cite:121][cite:124][cite:181]
- Cloud Monitoring, Cloud Logging, and Error Reporting for observability.[cite:88]

### Networking

- Keep public-facing services minimal: frontend, API, dashboard, webhook.
- Keep execution and internal services in private network patterns.
- Use service accounts per service with least-privilege IAM.
- Use authenticated Pub/Sub push or pull subscribers for internal fan-out.[cite:127]

## Africa-specific logic

Africa support should be implemented as policy packs and connectors, not as a separate product. The key differentiator is corridor-aware profitability, not only exchange spread detection.[cite:48][cite:51][cite:150]

Required Africa-specific service logic:

- Country policy rules and restrictions.[cite:40][cite:44]
- Stablecoin route selection and local rail compatibility.[cite:168][cite:171]
- Corridor FX conversion costs and payout-time confidence.[cite:150][cite:153]
- Country-aware onboarding requirements and review states.[cite:40][cite:44]
- Mobile-first dashboard assumptions for users in those markets.[cite:48]

Suggested Africa opportunity score inputs:

- gross spread
- maker/taker fees
- stablecoin conversion cost
- FX spread
- payout friction cost
- settlement time risk
- venue reliability score
- country policy confidence

## Global-specific logic

Global support should focus on deeper venue coverage, better order-book quality, and stronger execution analytics.[cite:131][cite:135][cite:167]

Required global service logic:

- exchange health scoring
- best-bid/best-ask synthesis
- order-book imbalance and liquidity checks
- venue eligibility by user region
- optional support for basis and cross-exchange strategies later

Suggested global opportunity score inputs:

- gross spread
- trading fees
- slippage estimate
- latency cost estimate
- funding cost where relevant
- liquidity confidence
- venue availability score
- inventory balance penalty

## Database domains

Split the relational schema into clear domains:

### Identity and tenant domain
- users
- clerk_users
- organizations
- tenant_memberships
- roles
- permissions

### Subscription and onboarding domain
- plans
- subscriptions
- invoices
- onboarding_profiles
- onboarding_events
- kyc_reviews
- disclosures

### Trading domain
- trading_accounts
- balances
- positions
- opportunities
- orders
- executions
- transfers
- withdrawals
- risk_limits
- kill_switch_events

### Audit and support domain
- audit_logs
- webhook_events
- support_notes
- compliance_flags

## Development order for Claude Code

### Phase 1 — skeleton and foundations

- Set up monorepo structure.
- Create service directories and shared library package.
- Create GCP infrastructure module skeletons.
- Implement Clerk session verification middleware.
- Implement Postgres models for users, tenants, onboarding, subscriptions, roles.
- Implement Clerk webhook ingestion and idempotent sync.

### Phase 2 — control plane

- Build frontend onboarding flow.
- Build auth session API.
- Build Stripe subscription flow.
- Build billing webhook processing with idempotency.
- Build onboarding status machine.
- Sync Clerk organizations and memberships into tenant mapping.

### Phase 3 — connectors and market ingestion

- Implement `connector-ccxt-service`.
- Implement `connector-africa-rails-service`.
- Implement `fx-feed-service`.
- Normalize and publish all events to Pub/Sub.
- Cache live state in Redis.

### Phase 4 — decision layer

- Implement opportunity engine.
- Implement risk engine.
- Implement entitlements and policy gates.
- Implement paper trading only first.

### Phase 5 — execution and ledger

- Implement execution orchestrator.
- Implement internal ledger and execution recording.
- Add kill switch and route-level safeguards.

### Phase 6 — intelligence and analytics

- Export execution and market events to BigQuery.
- Build dashboard analytics endpoints.
- Add route-quality analysis and missed-opportunity reports.

## Claude Code instructions

Claude Code should start with a monorepo and a service-oriented layout. Suggested structure:

```text
/apps
  /frontend
  /core-api
  /dashboard-api
  /billing-webhook
  /clerk-webhook-sync
/services
  /onboarding-service
  /connector-ccxt-service
  /connector-africa-rails-service
  /connector-onchain-service
  /fx-feed-service
  /normalizer-service
  /opportunity-engine
  /risk-engine
  /execution-orchestrator
  /ledger-service
  /alerting-service
/packages
  /shared-types
  /event-schema
  /auth
  /rbac
  /db
  /observability
  /config
/infra
  /terraform
/docs
```

Implementation guidance:

- Use TypeScript for frontend and control-plane APIs.
- Use Python for connector services and market microstructure logic if preferred.
- Standardize event payloads before any strategy logic runs.
- Keep strategy logic stateless where possible and store only hot state in Redis.
- Keep all secrets out of code and environment files when possible; use Secret Manager bindings and runtime retrieval.[cite:62][cite:121]
- Build every service with health checks, structured logs, and trace correlation.[cite:88]
- Use idempotency keys on all webhook and event-consumer write paths.[cite:109][cite:112][cite:179]
- Mirror Clerk users and organizations into local database tables so product logic does not depend on live Clerk lookups for every request.[cite:178][cite:179][cite:186]

## Initial non-goals

The first build should not include the following:

- High-frequency trading infrastructure
- microsecond colocation assumptions
- dozens of exchanges on day one
- full live auto-trading before paper-trading validation
- full custom ML ranking before there is execution data
- custody-heavy institutional features unless legally required

## MVP definition

An MVP is complete when the platform can:

- onboard a user through Clerk
- verify identity and activate a subscription
- route the user into Africa or global product mode
- stream live market data from at least one Africa connector and two global connectors
- compute normalized opportunities
- show live opportunities in a dashboard
- run paper trading with risk controls
- persist trades, events, and analytics

## Success criteria

Technical success metrics:

- connector uptime and reconnect reliability
- event processing lag
- Redis freshness for live order books
- false-positive opportunity rate
- paper-trade execution acceptance rate
- webhook idempotency correctness
- onboarding completion rate
- region-specific approval times

Business-facing success metrics:

- activation rate after sign-up
- paid conversion rate
- paper-to-live conversion rate
- corridor usage in Africa mode
- venue usage distribution in global mode
- churn by plan and region

## Final architecture principle

The platform should be treated as one GCP-native engine with regional intelligence overlays. Africa is primarily a stablecoin, FX, payout, and policy-routing problem; global is primarily an exchange-liquidity and execution-quality problem. The technical design should preserve one shared backbone while allowing connector packs and scoring models to diverge by market.[cite:48][cite:51][cite:131]


## AI-driven architecture

The platform should be designed as AI-augmented from the start and AI-driven over time. The deterministic core remains responsible for spread detection, fee math, slippage assumptions, balance checks, entitlement checks, policy restrictions, and kill-switch enforcement. AI should improve ranking, anomaly detection, route optimization, and operational intelligence rather than replacing hard controls.[cite:196][cite:198][cite:203]

### AI design principle

Use AI where prediction improves quality, but keep rules where safety, compliance, and auditability matter most.[cite:197][cite:203]

Recommended split:

- Deterministic layer: raw spread math, fee calculation, FX normalization, balance availability, compliance rules, risk caps, kill switch.[cite:198][cite:203]
- AI layer: opportunity quality scoring, anomaly detection, route ranking, execution success prediction, corridor reliability prediction, and operator copilots.[cite:191][cite:192][cite:196]

### AI use cases

#### 1. Opportunity ranking

Build a model that predicts whether an observed opportunity is likely to remain profitable after real execution costs. Inputs can include:

- raw spread
- top-of-book depth
- book imbalance
- recent volatility
- venue latency
- historical slippage
- venue health score
- route type
- region
- time-of-day

Output:

- `profitability_probability`
- `expected_net_bps`
- `confidence_score`

This model should score opportunities after deterministic filtering but before final approval.[cite:196][cite:201]

#### 2. Venue anomaly detection

Use anomaly detection to identify stale books, broken connectors, abnormal volume, bad sequence streams, lagging venues, or suspicious fill behavior. This can reduce false positives and protect execution quality.[cite:191][cite:192]

Suggested anomaly signals:

- feed heartbeat drift
- spread jump outside expected band
- sequence mismatch rate
- order-book update frequency drop
- quote staleness duration
- abnormal execution rejection rate

#### 3. Route optimization

Use historical execution outcomes to rank the best route for a candidate trade. This is especially useful when several venues or rails can satisfy the same opportunity.[cite:197][cite:203]

Suggested route features:

- venue pair
- region
- asset pair
- expected depth
- order size bucket
- prior fill rate
- prior partial-fill rate
- average slippage
- time-to-completion

Output:

- `recommended_route`
- `predicted_fill_quality`
- `predicted_slippage_bps`
- `predicted_completion_time`

#### 4. Africa corridor intelligence

For Africa mode, AI can be especially useful for predicting settlement confidence and corridor friction, because the real edge often depends on payout delay, FX spread drift, and local rail reliability rather than only exchange spread.[cite:48][cite:51][cite:150]

Suggested Africa-specific model outputs:

- `corridor_reliability_score`
- `predicted_settlement_time`
- `predicted_payout_friction`
- `stablecoin_route_preference`

#### 5. Ops and support copilots

Use AI agents internally for platform operations, not direct trade authority. These agents can triage feed failures, summarize venue incidents, classify webhook issues, explain failed opportunities, and detect operational regressions from logs and metrics.[cite:193][cite:200]

## AI services to add

Add these services after the core deterministic platform is functional:

### New services

- `feature-extraction-service`
- `opportunity-ranker`
- `venue-anomaly-detector`
- `route-optimizer`
- `corridor-intelligence-service`
- `ops-copilot-service`
- `model-feedback-exporter`

### Responsibilities

| Service | Responsibility |
|---|---|
| `feature-extraction-service` | Build online and batch features from market, execution, and onboarding events |
| `opportunity-ranker` | Score candidate opportunities before final risk approval |
| `venue-anomaly-detector` | Flag degraded feeds, stale books, or abnormal venue behavior |
| `route-optimizer` | Predict best route by venue, rail, size, and timing |
| `corridor-intelligence-service` | Predict Africa-specific payout and corridor conditions |
| `ops-copilot-service` | Assist internal operators with incident explanations and summaries |
| `model-feedback-exporter` | Export labels and outcomes for retraining |

## AI data design

Use BigQuery as the primary analytical and training substrate. The event-driven architecture already produces the right primitives for ML if all decisions and outcomes are captured cleanly.[cite:86][cite:200]

### Feature sources

- market data events
- order-book state snapshots
- FX rate events
- venue health events
- risk decisions
- execution results
- partial-fill and cancel events
- onboarding and region metadata
- corridor settlement outcomes

### Core training tables

- `fact_market_events`
- `fact_orderbook_snapshots`
- `fact_opportunities`
- `fact_risk_decisions`
- `fact_executions`
- `fact_route_outcomes`
- `fact_corridor_settlements`
- `dim_venues`
- `dim_assets`
- `dim_corridors`
- `dim_users`

### Labels to capture

- realized net profit or loss
- slippage in basis points
- execution success or failure
- time to completion
- partial fill ratio
- stale-opportunity rate
- payout settlement delay
- route abandonment reason

## Online vs batch AI

Split AI into online scoring and offline training.

### Online scoring

Online scoring should be fast and lightweight. It is used inside the live trade path after deterministic filtering and before final risk approval.[cite:196][cite:203]

Recommended online inference targets:

- opportunity ranking
- route ranking
- anomaly scoring
- corridor reliability scoring

### Offline training

Offline pipelines should use BigQuery-derived training sets and periodic retraining. Start with daily or weekly retraining, not continuous training.[cite:200]

Recommended offline workflows:

- feature extraction batch jobs
- label generation jobs
- model evaluation
- champion/challenger comparison
- model promotion only after validation

## Optional GCP AI tooling

Do not make Vertex AI mandatory on day one, but it is a good fit later for managed training, model serving, and experimentation once enough data exists.[cite:199][cite:200]

Practical GCP AI path:

- Phase 1: BigQuery + Python training notebooks/scripts
- Phase 2: Cloud Run inference service for simple models
- Phase 3: Vertex AI for managed training and endpoint hosting if model volume and governance needs increase

## AI-safe control flow

AI must never directly bypass hard safety controls. The order of operations should be:

1. Connector data arrives.
2. Deterministic normalization and cost math runs.
3. Candidate opportunity is produced.
4. AI scoring ranks quality and route preference.
5. Risk engine applies hard rules.
6. Execution orchestrator submits or suppresses action.
7. Outcomes are logged for learning.

The risk engine remains final authority for trade approval.[cite:197][cite:203]

## AI-related event schema additions

Add these event types:

- `features.computed`
- `opportunity.scored`
- `venue.anomaly.detected`
- `route.recommended`
- `corridor.scored`
- `model.inference.failed`
- `model.feedback.captured`
- `ops.incident.summarized`

Suggested AI scoring payload:

```json
{
  "event_id": "uuid",
  "event_type": "opportunity.scored",
  "timestamp": "ISO-8601",
  "opportunity_id": "uuid",
  "model_version": "ranker-v1",
  "profitability_probability": 0.84,
  "expected_net_bps": 18.2,
  "confidence_score": 0.79,
  "recommended_action": "continue_to_risk",
  "top_features": [
    "venue_health_score",
    "top_of_book_depth",
    "historical_slippage"
  ]
}
```

## AI rollout phases

### AI Phase A — instrumentation first

- Ensure every opportunity, risk decision, and execution outcome is logged.
- Build clean BigQuery facts and dimensions.
- Build replay tooling.
- Do not launch ML models yet.

### AI Phase B — anomaly detection and scoring

- Add venue anomaly detection.
- Add first opportunity ranking model.
- Add operator-only route recommendations.
- Keep live execution guarded by deterministic approval.

### AI Phase C — production optimization

- Add route optimizer into live flow.
- Add corridor intelligence for Africa mode.
- Add model monitoring and challenger models.
- Add ops copilot for incident and support workflows.

## Updated development order for Claude Code

### Phase 1 — skeleton and foundations

- Set up monorepo structure.
- Create service directories and shared library package.
- Create GCP infrastructure module skeletons.
- Implement Clerk session verification middleware.
- Implement Postgres models for users, tenants, onboarding, subscriptions, roles.
- Implement Clerk webhook ingestion and idempotent sync.
- Instrument domain events from the beginning so ML labels exist later.

### Phase 2 — control plane

- Build frontend onboarding flow.
- Build auth session API.
- Build Stripe subscription flow.
- Build billing webhook processing with idempotency.
- Build onboarding status machine.
- Sync Clerk organizations and memberships into tenant mapping.

### Phase 3 — connectors and market ingestion

- Implement `connector-ccxt-service`.
- Implement `connector-africa-rails-service`.
- Implement `fx-feed-service`.
- Normalize and publish all events to Pub/Sub.
- Cache live state in Redis.
- Persist replayable event facts in BigQuery.

### Phase 4 — deterministic decision layer

- Implement opportunity engine.
- Implement risk engine.
- Implement entitlements and policy gates.
- Implement paper trading only first.

### Phase 5 — AI enablement

- Implement `feature-extraction-service`.
- Build first training datasets in BigQuery.
- Implement `venue-anomaly-detector`.
- Implement `opportunity-ranker` as advisory first.

### Phase 6 — execution and learning loop

- Implement execution orchestrator.
- Implement internal ledger and execution recording.
- Add kill switch and route-level safeguards.
- Capture route outcomes and model feedback for retraining.

### Phase 7 — intelligence and optimization

- Build dashboard analytics endpoints.
- Add route-quality analysis and missed-opportunity reports.
- Promote AI scoring from advisory to integrated ranking where validated.

## Updated Claude Code implementation guidance

- Design every event and database write so it can become a feature or label later.
- Keep AI inference as a separate service boundary, not embedded deeply inside risk code.
- Use explainable model outputs and persist model version for every scored opportunity.
- Fail open or fail safe intentionally: if ranking fails, deterministic filtering still works; if anomaly detection fails, risk rules still apply.
- Do not allow LLMs to send trades directly.
- Restrict agentic AI to operations, analysis, summarization, and recommendation layers until the platform has mature controls.[cite:193][cite:197][cite:203]
