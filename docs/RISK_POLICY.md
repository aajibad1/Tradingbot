# Risk Policy

Owner: `risk-engine` service.
Scope: every opportunity flowing toward `paper-trader` or
`execution-orchestrator` must pass through `POST /evaluate` first. There is no
side-door — even the AI Ops agent is read-only on Redis and cannot bypass
this layer.

## 1. Canonical limits

These values are defined exactly once, in
`services/risk-engine/rules/drawdown_guard.py::DEFAULT_LIMITS`. Anything else
in this document or in code that disagrees is a bug.

| Limit | Default | Rationale |
| --- | ---: | --- |
| `max_daily_loss_pct` | 1.0% | Caps a single bad day. Breaching this trips the kill switch — manual intervention is required before trading resumes. |
| `max_per_trade_capital_pct` | 2.0% | Bounds the worst-case loss on any one position to ~2% of capital before considering hedge slippage. |
| `max_exchange_exposure_pct` | 25.0% | Limits concentration on any single venue so that a single exchange outage / freeze / insolvency cannot wipe out more than a quarter of capital. |
| `max_single_asset_exposure_pct` | 20.0% | Limits idiosyncratic asset risk (de-pegs, hard forks, listing halts). |
| `max_leverage_multiplier` | 1.0x | Paper-trader and the initial live phase run unlevered. Raising this is a policy decision that requires Slack approval (see Section 5). |
| `min_net_edge_bps` | 8.0 bps | Below this, the spread does not reliably clear fees + slippage + safety buffer. Matches `shared/utils/fee_calculator.MIN_VIABLE_NET_EDGE_BPS`. |
| `max_api_latency_ms` | 500 ms | A leg whose API is sluggish cannot be hedged in time. Sourced from `health:latency:<exchange>` written by `market-data`. |

## 2. Evaluation order

`POST /evaluate` runs the following stages in order. The order is part of
the contract — do not reorder without a corresponding update to CLAUDE.md.

1. **Kill switch** — if `risk:kill_switch:active == "1"`, the request returns
   immediately with `approved=false`, `kill_switch_active=true`, and an empty
   violations list. No other rule even runs.
2. **Position limits** (`rules/position_limits.py`):
   trade size, exchange exposure, asset concentration, leverage, min net edge.
3. **Drawdown** (`rules/drawdown_guard.py`): daily-loss check. *A breach trips
   the kill switch synchronously inside the same request* so the response
   surfaces both the violation and `kill_switch_active=true`.
4. **Exchange health** (`rules/exchange_health.py`): both the long and short
   leg's latency in Redis (`health:latency:<exchange>`) must be present AND
   below `max_api_latency_ms`. A missing key is treated as a violation
   (fail-closed).

## 3. Kill switch

### Trigger conditions

The kill switch is activated by `trigger_kill_switch(reason)` whenever:

- A daily-loss breach is detected during `/evaluate` (automatic, synchronous).
- An operator calls `POST /kill-switch/trigger` manually.
- The AI Ops agent calls its `trigger_kill_switch` tool (the one and only
  WRITE tool it is permitted to invoke).
- A downstream service detects an unrecoverable invariant violation and
  manually trips the switch.

### Side-effects of a trigger

Every successful trigger (idempotent — duplicates are no-ops) performs five
durable side-effects:

1. Sets `risk:kill_switch:active = "1"` in Redis with no expiry.
2. Sets `risk:kill_switch:metadata` to a JSON blob `{triggered_by, reason,
   triggered_at}`.
3. Publishes a `KILL_SWITCH_ACTIVATED` event to `Topic.RISK_ALERTS`
   (`arb-risk-alerts`) with attributes `event=KILL_SWITCH_ACTIVATED`,
   `source=risk-engine`. Failure to publish is logged but does not block the
   Redis write — Redis is the contract.
4. Sends a Slack alert via `SLACK_WEBHOOK_URL` (best-effort; skipped silently
   if unset, which is the expected state in local dev).
5. Emits a `CRITICAL`-level log line for Cloud Logging.

`GET /healthz` returns `503` while the switch is active so Cloud Run / load
balancers stop sending traffic to the instance.

### Reset procedure

Resets are deliberately friction-laden. There is no UI bypass.

1. The on-call engineer investigates the trigger reason (see
   `risk:kill_switch:metadata` and Cloud Logging).
2. They reconcile open positions in `risk-engine`'s `/status` and BigQuery
   trade-ledger views.
3. If safe to resume, they call
   `POST /admin/reset` (alias of `POST /kill-switch/reset`) with body:

   ```json
   {"auth_token": "<KILL_SWITCH_RESET_TOKEN>", "reset_by": "<name>"}
   ```

4. The token is fetched from Secret Manager (`KILL_SWITCH_RESET_TOKEN`).
   A wrong or missing token returns HTTP 403 and logs a warning. An unset
   env-var returns HTTP 500 — that is a misconfiguration, not a normal
   reject path.
5. On success, a Slack notification is sent acknowledging the reset.

## 4. On-call escalation

| Event | First responder | Escalation (15 min) | Escalation (1 h) |
| --- | --- | --- | --- |
| Kill switch tripped (drawdown) | Primary on-call | Secondary on-call | Trading lead |
| Kill switch tripped (manual / unknown) | Primary on-call | Trading lead | — |
| `/healthz` 503 for > 5 min and switch is NOT active (Redis down) | Primary on-call | Platform on-call | — |
| Repeated `api_latency` rejections on a single venue | Primary on-call | Exchange-relations owner | — |
| `risk:capital_usd` unset alert | Primary on-call | Trading lead | — |

All four Cloud Logging alerts route to `#crypto-arb-alerts` Slack.

## 5. AI Ops agent permission model

The AI Ops agent is read-mostly. The full tool inventory lives in
`services/ai-ops-agent/`; below is the summary that risk-engine enforces.

| Capability | READ | PROPOSE | WRITE |
| --- | :---: | :---: | :---: |
| Market data, funding rates, opportunities | yes | — | no |
| Risk state (`risk:*` keys, `/status`, `/state`) | yes | — | no |
| Trade ledger, BigQuery views | yes | — | no |
| Risk-limit changes | — | yes (Slack approval gate) | no |
| Trade-size / leverage changes | — | yes (Slack approval gate) | no |
| Trigger kill switch | — | — | yes (the *only* write tool) |
| Reset kill switch | — | — | no (humans only) |
| Withdrawals, transfers | — | — | hardcoded blocked |
| Increasing leverage beyond `max_leverage_multiplier` | — | — | hardcoded blocked |

A PROPOSE-only action posts a structured Slack message; an operator must
react with the configured emoji within 15 minutes for the change to apply.
No emoji = no change.

## 6. Audit-log expectations

Every transition that affects the trading authority is auditable:

- Kill switch trigger and reset → `Topic.RISK_ALERTS` AND CRITICAL log line.
- Opportunity rejection → INFO log line with `id` and rule list. We do not
  publish rejection events to Pub/Sub by default (volume) — Cloud Logging is
  the source of truth.
- Opportunity approval → INFO log line; the approved opportunity is the next
  message published downstream and is itself the audit record.
- Manual `POST /kill-switch/trigger` and `POST /admin/reset` → log
  `triggered_by` / `reset_by` and route through Cloud Logging audit sinks.

BigQuery is **not** written from `risk-engine`. `trade-ledger` is the sole
BigQuery writer in the system; long-term audit retention is its
responsibility, sourced from `Topic.AUDIT_LOG` and `Topic.TRADE_FILLS`.

## 7. Required environment

| Variable | Required in prod | Notes |
| --- | :---: | --- |
| `REDIS_URL` | yes | defaults to `redis://localhost:6379/0` for dev |
| `KILL_SWITCH_RESET_TOKEN` | yes | fetched from Secret Manager |
| `SLACK_WEBHOOK_URL` | recommended | unset = no Slack alerts (local dev) |
| `GCP_PROJECT_ID` | yes | unset = `NullPublisher` (logs only) |

Redis MUST contain `risk:capital_usd` (set externally by the bootstrap
script) before `/evaluate` will work — `load_state` raises `RuntimeError`
otherwise. This is intentional: an unconfigured system must never look like
a valid one.
