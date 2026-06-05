# Exchange onboarding — "Connect Exchange" via an aggregator (Mesh / Front)

**Status:** roadmap / design sketch. Not yet built. Slots on top of the existing
per-user-secrets model (`shared/tenant.py`, `MULTI_TENANCY.md`) without changing
it.

## Why this exists

The system is **non-custodial**: each user trades their own funds with their own
exchange API keys. We already model that — `exchange_secret_id(ex, kind, tenant)`
→ `user-{uid}-{ex}-key` in Secret Manager, read by
`get_exchange_credentials(exchange, tenant_id)` and used through one CCXT Pro
interface. A unified *trading* API (Talos/CoinRoutes-style) is the **wrong** tool
here — it implies custody (money-transmitter licensing) and adds a per-trade toll
that destroys an 8 bps net edge (see `TRADING_ASSUMPTIONS.md`).

The real onboarding pain is UX: making a user **paste an API key + secret** per
exchange is error-prone (wrong permissions, withdrawal left enabled, typos). An
**account-connection aggregator** — **Mesh Connect** or **Front Finance**, the
"Plaid for exchanges" — fixes *that* and nothing else: the user clicks
**"Connect Binance"**, authenticates in a hosted widget, and we receive scoped,
**withdrawal-disabled** API credentials. Under the hood it is still
per-user/per-exchange creds landing in the exact same Secret Manager slots. It is
an **additive UX layer**, not an architecture change.

## What changes vs. what stays

| Layer | Today (manual keys) | With aggregator | Changes? |
|---|---|---|---|
| Secret storage | `user-{uid}-{ex}-key/secret` | same ids, same values | **No** |
| Credential read | `get_exchange_credentials(ex, tenant)` | unchanged | **No** |
| Order placement | CCXT Pro per-user keys | unchanged | **No** |
| Risk/tenancy | `risk:t:{uid}:*`, kill switch | unchanged | **No** |
| **How the key gets *into* the slot** | user pastes into a form | aggregator OAuth-style link → callback writes the slot | **New** |

Only the **write path into Secret Manager** is new. Everything downstream is
already tenant-aware and untouched.

## New component: `account-link-service` (thin)

A small FastAPI service (mirrors `fx-rate-service` / `notification-dispatcher`
shape: `main.py` + `requirements.txt` + `Dockerfile`, internal ingress) that
brokers the aggregator handshake. It is the **only** service that writes
`user-{uid}-{ex}-*` secrets — same single-writer discipline as risk-engine for
Redis.

Endpoints:

```
POST /link/session      body: {user_id, exchange}      → {link_url, link_token}
        Ask the aggregator (Mesh/Front) for a hosted-widget session scoped to
        trade-only + read; return the URL the frontend opens.

POST /link/callback     (aggregator webhook, signed)    → 200
        Aggregator delivers scoped credentials for {user_id, exchange}.
        1. verify webhook signature (aggregator secret in Secret Manager)
        2. assert the granted scope is TRADE/READ and NOT withdrawal — reject otherwise
        3. write user-{uid}-{ex}-key / -secret (/ -password) via
           gcloud/secretmanager AddVersion  (create container on first link)
        4. grant the per-tenant Cloud Run SA accessor IAM on those secret ids
        5. mark the connection 'active' in per-user config (Firestore, per TDD)

GET  /link/status?user_id=&exchange=    → {connected, scope, last_verified}
DELETE /link/{user_id}/{exchange}       → revoke at aggregator + disable secret versions
```

### Onboarding sequence

```
Frontend            account-link-service        Aggregator (Mesh/Front)     Secret Manager
   │  Connect Binance      │                          │                          │
   ├──POST /link/session──▶│                          │                          │
   │                       ├──create link session────▶│                          │
   │                       │◀────link_url─────────────┤                          │
   │◀──link_url────────────┤                          │                          │
   ├───────open hosted widget, user auths at exchange────────▶│                  │
   │                       │◀═══signed webhook (scoped creds)══┤                 │
   │                       │  verify sig + assert no-withdrawal scope            │
   │                       ├──AddVersion user-{uid}-binance-key/secret──────────▶│
   │                       ├──grant tenant SA accessor IAM────────────────────-─▶│
   │◀──status: connected───┤                          │                          │
```

After this, the existing path just works: `opportunity → risk-engine (tenant) →
execution-orchestrator → get_exchange_credentials(ex, tenant) → CCXT`.

## Security invariants (must hold)

- **Withdrawal scope is rejected, not just discouraged.** The callback refuses
  any grant whose scope includes withdrawal/transfer. Defence in depth on top of
  the exchange-side setting and the hardcoded-blocked withdrawal rule in
  `docs/ARCHITECTURE.md`.
- **No cross-tenant fallback.** `get_exchange_credentials` already forbids a user
  tenant from reading `arb-*` platform keys — keep that. A failed link ⇒ that
  user simply can't trade that venue; never silently use shared keys.
- **Single writer.** Only `account-link-service` writes `user-*` secrets. No
  other service gets `secretmanager.versions.add` on them.
- **Webhook auth.** Aggregator webhooks are signature-verified; the signing
  secret lives in Secret Manager (`mesh-webhook-secret` / `front-webhook-secret`).
- **Least-privilege accessor.** Each user's Cloud Run consumer SA gets
  `secretAccessor` only on that user's secret ids (extends the existing
  per-service `google_secret_manager_secret_iam_member.accessor` pattern).

## Provider note: Mesh vs Front

Both do OAuth-style connect + scoped trading access across major CEXes; both
deliver creds we persist. Pick on: exchange coverage (must cover Coinbase,
Kraken, Crypto.com, Binance.US, and ideally Hyperliquid), pricing per linked
account, and webhook-vs-poll delivery. Coverage gaps fall back to the **manual
key-paste form** we have today — so onboarding is aggregator-first,
manual-fallback. Keep both paths.

## Sequencing (do NOT pull forward)

This belongs in **Phase 4 — Platform services** of `MULTI_TENANCY.md` (auth,
config, billing). Prerequisites that must land first:

1. Phases 1–3 of `MULTI_TENANCY.md` (tenant primitive ✓, identity on the wire,
   per-user secret reads).
2. Auth (Firebase) + per-user config store (Firestore) — the callback needs a
   real `user_id` and somewhere to record connection state.

## ⚠️ Validation gate still applies

Smoother onboarding lets *more* users connect — it **does not create edge**. No
linked account is promoted to **live** trading until the strategy clears the
backtest gate (Sharpe > 1.5) and a live paper run, per `TRADING_ASSUMPTIONS.md`.
A connected exchange defaults to **paper**; live is gated per tenant by
`ENABLE_AUTONOMOUS_EXECUTION` / `LIVE_SINCE`.
