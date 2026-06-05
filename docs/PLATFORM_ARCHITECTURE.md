# Platform architecture — multi-tenant, two-market (Africa + Global)

**Status:** target architecture / build spec. Captures the control-plane,
execution-plane, and onboarding flow the platform is being built toward. The
existing repo is the **Global execution spine in paper mode**; this doc records
what is reused, what is net-new, and the order we build it in. Pairs with
`MULTI_TENANCY.md` (tenant primitive), `EXCHANGE_ONBOARDING.md` (key linking),
and `TRADING_ASSUMPTIONS.md` (the live-promotion gate).

## Two markets are two engines on one core

"Arbitrage" means different things per market — do not conflate them:

- **Global market** — crypto funding-carry / spot-perp basis / cross-exchange.
  Delta-neutral, edge = funding/basis, execution = order routing on exchanges
  with the user's own (withdrawal-disabled) keys. **This is what the repo does
  today, in paper mode.**
- **Africa market** — cross-border FX / stablecoin **corridor** arbitrage. Edge =
  FX + corridor spread minus conversion cost and **settlement delay**; risk is
  settlement/AML/payout, not funding. Mostly **alert-only / guarded**, not
  auto-execution. **Greenfield — almost nothing in the repo yet.**

They share one platform core (Cloud Run + Pub/Sub + Redis + BigQuery + the
tenant/secret/risk primitives in `shared/`). The market-specific parts hang off
that core behind a **regional policy engine**.

## Control plane (NEW — not in the repo today)

> **Canonical product brief:** `docs/claude-code-starter-doc-clerk-ai.md` (Clerk
> auth, managed-custody SaaS, AI plane). This doc reconciles that brief against
> what the repo already has. Where the brief conflicts with an existing
> documented decision, see "Decisions that change the current design" below.

| Component | Role | Store | Status |
|---|---|---|---|
| Identity (**Clerk**) | user authN, sessions, MFA, orgs; verified via JWKS | — | NEW |
| Clerk webhook sync | mirror Clerk users/orgs → Postgres (idempotent) | Postgres | NEW |
| **Core API** (Cloud Run) | verify token, user/tenant/RBAC, profile | Cloud SQL Postgres | NEW |
| RBAC / entitlements | roles, per-tenant limits, plan gating | Cloud SQL Postgres | NEW |
| **Onboarding service** | KYC/KYB + region + funding workflow | Postgres + Pub/Sub | NEW (`account-link-service` is the *key-link* slice, today a 501 skeleton) |
| Stripe billing + webhook | subscription → entitlement | Postgres | NEW |
| Regional policy engine | Africa / Global eligibility + AML/sanctions/venue gates | Postgres | NEW |
| Audit / compliance log | immutable action log | Postgres + BigQuery | partial (BigQuery audit topic exists) |

**Key delta from the current stack:** the platform introduces **Cloud SQL
Postgres** as the transactional store for identity, RBAC, accounts, and
onboarding state. The repo today has **no relational DB** — risk state is in
Redis and history is in BigQuery. Postgres is additive: Redis stays the hot risk
state, BigQuery stays the analytics/event history, Postgres becomes the system of
record for *who the user is and what they're allowed to do*.

## Execution plane (REUSE — mostly built)

| Component | Repo service | Status |
|---|---|---|
| Market feed collectors | `services/market-data` (CCXT Pro WS) | ✅ built |
| FX / corridor feed | `services/fx-rate-service` (FX only) | 🟡 FX built; corridor/P2P NEW |
| Normalizer / enrichment | inline in opportunity-engine + `shared/utils/exchange_normalizer` | 🟡 partial; a dedicated normalizer is NEW |
| Opportunity engine | `services/opportunity-engine` (3 strategies) | ✅ built (Global) |
| Risk engine | `services/risk-engine` | ✅ built; **+ entitlement/policy checks NEW** |
| Execution orchestrator | `services/execution-orchestrator` | 🟡 marshals Hummingbot orders; client is a stub |
| Trade ledger / accounts | `services/trade-ledger` (BigQuery) | 🟡 analytics facts built; **transactional accounts in Postgres NEW** |
| Pub/Sub bus, Redis, BigQuery, Monitoring | `shared/pubsub`, terraform modules | ✅ built |

## Two known gaps in the current Global path (fix before live)

1. **Approve → execute bridge is missing.** `risk-engine /evaluate` returns
   `approved: bool` but **nothing republishes the opportunity with
   `execute=True`**, and paper-trader / execution-orchestrator only act on
   `execute=True`. Today only scripts set that flag. A risk-approval must
   republish (or a thin executor must consume approvals).
2. **Execution is a stub.** `execution-orchestrator/hummingbot_client.py` POSTs to
   a Hummingbot gateway that isn't in the repo. No real orders place.

## Onboarding flow (the sequence we build first)

Per the target sequence: `Auth → Core API → Postgres (user+tenant+role) → Stripe
checkout → webhook → KYC/KYB + region + funding → onboarding workflow → regional
policy eval → trading_ready | pending_review`. A connected exchange defaults to
**paper**; live is gated per tenant by `ENABLE_AUTONOMOUS_EXECUTION` / `LIVE_SINCE`
**and** a passed validation gate (`TRADING_ASSUMPTIONS.md`).

### Build increments (onboarding-first)

1. **Identity + tenant + RBAC foundation** — `services/core-api`: verify Firebase
   token (local-dev bypass), idempotently create tenant+user+default role in
   Postgres, `GET /me`. Tenant ids conform to `shared.tenant.validate_tenant` so
   Redis/Secret-Manager namespacing is consistent. *(this increment)*
2. **Onboarding workflow** — KYC/KYB + region + funding capture; persist
   onboarding state; publish `onboarding.submitted`; mark
   `trading_ready | pending_review`.
3. **Regional policy engine** — Africa vs Global eligibility, AML/sanctions
   screening hooks, per-jurisdiction venue/product gates; consulted by both
   onboarding and risk-engine.
4. **Billing** — Stripe checkout + signed webhook → subscription → entitlements.
5. **Exchange key linking** — finish `account-link-service` (aggregator +
   manual-key fallback) on top of (1)–(3).

## Decisions that change the current design (need an explicit call)

The Clerk/AI brief diverges from decisions already baked into this repo. Three
are consequential; do not let them slide in silently:

1. **Custody model — DECIDED: managed custody.** *"Users do not bring exchange
   API keys; the platform owns the integrations through backend-managed
   credentials."* This **supersedes** the non-custodial design in
   `EXCHANGE_ONBOARDING.md` — that doc (and the per-user-key
   `account-link-service`) is now **out of scope for the launch model**. The
   managed model means:
   - **The platform trades on its own exchange accounts** (the existing `arb-*`
     platform keys in Secret Manager), not per-user keys. `get_exchange_credentials`
     is used with the **platform tenant**, not per-user tenants.
   - **Per-user money is an internal ledger problem**: tenant balances, positions,
     and a pooled-vs-segregated treasury — tracked in Postgres (`trading_accounts`,
     `balances`, `transfers`, `withdrawals` per the brief), reconciled against the
     platform's real exchange balances.
   - **Onboarding gains a real funding step** (`funding_pending`) and **payouts**.
   - **This is a regulated undertaking** — holding user funds and trading them is
     money-transmitter / VASP (and possibly broker-dealer / collective-investment,
     per jurisdiction) territory. The legal/licensing workstream **gates live
     money**; engineering can proceed on paper-mode + the internal ledger in
     parallel. See the trade-off note the team was given.
2. **Auth provider — Clerk (resolved).** Was Firebase in the first sketches; the
   brief settles on Clerk. Cheap and isolated: it's one adapter in
   `services/core-api/auth.py` (`AUTH_PROVIDER=clerk`) plus a `clerk-webhook-sync`
   service. The identity→tenant→RBAC core does not care which IdP it is.
3. **Language / layout — Python stays for the engine.** The brief suggests a
   TS `/apps`+`/packages` monorepo with TS control-plane APIs. The execution
   plane (market-data, opportunity-engine, risk-engine, paper-trader, ledger) is
   **already built and tested in Python** and should NOT be rewritten.
   **Recommendation:** keep Python services under `services/`; add TS only for
   the `frontend`. core-api can stay FastAPI (as built) or move to NestJS — that's
   a smaller call than rewriting the engine.

## Execution posture (near-term, deliberate)

- **Global: paper-only** until the approve→execute bridge + a real Hummingbot
  instance exist AND the per-tenant validation gate passes.
- **Africa: alert-only** — detect corridor spreads and notify; **no automated
  payouts** until KYC/AML + the policy engine + payout-rail reconciliation exist.
  Real settlement is money-transmission/VASP-regulated and country-specific.
- Live money is blocked on identity + compliance regardless, which is why
  onboarding is built before execution.
