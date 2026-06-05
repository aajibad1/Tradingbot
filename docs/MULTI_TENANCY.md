# Multi-tenancy foundation

The current engine is **single-tenant**: one capital pool, one set of exchange
keys, one global Redis risk state, one kill switch. The ArbitrageAI platform
(see `ArbitrageAI-TDD-v2.md`) requires **per-user isolation** of secrets, risk
state, capital, positions, and P&L. This document defines the tenancy model and
the incremental path from the single-tenant engine to it — *without* a
big-bang rewrite.

## Tenant model

- A **tenant** is identified by an opaque `tenant_id` (the platform `user_id`).
- A reserved **`default`** tenant preserves today's single-tenant behaviour and
  on-disk Redis keys, so the existing deployment keeps working with zero data
  migration while multi-tenant users get namespaced state.
- A reserved **`platform`** tenant owns global controls — chiefly the
  **platform-wide kill switch**, which halts *every* tenant.

## Key & secret schema (`shared/tenant.py` is the single source)

| Concern | `default` tenant (legacy, unchanged) | per-tenant |
|---|---|---|
| Risk state | `risk:capital_usd` | `risk:t:{tenant}:capital_usd` |
| Exposure scan | `risk:exposure:*` | `risk:t:{tenant}:exposure:*` |
| Per-user kill switch | `risk:kill_switch:active` | `risk:t:{tenant}:kill_switch:active` |
| Platform kill switch | `platform:kill_switch:active` (global, all tenants) | — |
| Exchange secret id | `arb-{exchange}-key` | `user-{tenant}-{exchange}-key` |

GCP Secret Manager names must match `[A-Za-z0-9_-]`, so the TDD's logical path
`/users/{uid}/exchanges/{ex}/api_key` maps to the flat id `user-{uid}-{ex}-key`.

## Kill-switch precedence

A trade is blocked if **either** the platform kill switch **or** the tenant's
own kill switch is active. The risk-engine checks `platform` first
(short-circuit), then the tenant.

## Incremental rollout

1. **Foundation (this change):** `shared/tenant.py` primitive + make the
   **risk-engine** (the choke point that owns `risk:*`) tenant-aware. `default`
   tenant ⇒ legacy keys ⇒ existing behaviour/tests unchanged.
2. **Identity on the wire:** add `user_id` to `Opportunity`/`Trade` (+ BigQuery
   columns, partitioned by `user_id`) so the whole data flow is tenant-tagged.
3. **Per-user secrets:** tenant-aware `get_exchange_credentials(tenant, …)` in
   the data-ingestion / execution services.
4. **Platform services:** auth (Firebase), per-user config (Firestore), billing,
   notifications, FX — the SaaS shell, per the TDD roadmap.

## Backward compatibility & safety

- `tenant_id` defaults to `default` everywhere, so untenanted callers and the
  current single-tenant deployment are unaffected.
- The platform kill switch is **additive** — it can only *add* a halt condition,
  never remove the existing per-tenant one.
- No Redis key migration is required for the existing deployment.

## ⚠️ Validation gate (non-negotiable)

Per `TRADING_ASSUMPTIONS.md` and the real-data analysis, arbitrage edge is rare
and currently **unvalidated**. Multi-tenancy lets *more* users run the engine —
it does **not** create edge. No tenant should be onboarded to *live* trading,
and no profitability claim should be surfaced, until a strategy clears the
backtest gate (Sharpe > 1.5, etc.) and a live paper run. The supervised-execution
gate (`ENABLE_AUTONOMOUS_EXECUTION` / `LIVE_SINCE`) applies per tenant.
