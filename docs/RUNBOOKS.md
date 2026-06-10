# Runbooks — operational procedures (expands docs/11)

Concrete, repo-grounded procedures for the runbooks docs/11 lists. Every command and
endpoint below is real in this repo. Severity model follows the status taxonomy:
**ok / degraded / down** (docs/SLOS.md). Reliability is the #1 priority — these are
how we *prove* the controls, not just document them.

Quick reference:
- Platform health: `GET status-service:8087/status` (ok=200, degraded=200, down=503).
- SLO burn: `POST status-service:8087/slo/evaluate`; catalog at `/slo/catalog`.
- Local smokes (verify before/after a change): `failover_smoke.sh`,
  `paper_trade_local.sh`, `api_plane_smoke.sh`, `governance_smoke.sh`,
  `control_plane_smoke.sh`.

---

## Incident severity triage
1. Hit `GET status-service:8087/status`. The aggregate `status` field is your severity:
   - **down (503)** — a *critical* dependency is down; external monitors are already
     tripping. SEV-1. Find the `components[]` entry with `critical: true, status: down`.
   - **degraded (200)** — a *non-critical* dependency is down; platform still serves.
     SEV-2/3. The platform is up; fix the named component without emergency rollback.
   - **ok (200)** — green; if a customer reports an issue, it's not a platform-health one.
2. Check SLO burn: `POST /slo/evaluate` with observed good/total — a `page` verdict
   (fast burn) is SEV-1 regardless of instantaneous health.
3. Open an incident channel; assign a single commander; post the `/status` body.

## Kill switch — trip / reset (trading)
- **Trips automatically** when the drawdown guard breaches inside risk-engine `/evaluate`
  (synchronous), or manually via the admin endpoint. Once active, no opportunity is
  forwarded to executors — the system is locked down by design.
- **Reset (deliberate, audited):**
  ```
  curl -s risk-engine:8082/kill-switch/reset -H 'content-type: application/json' \
    -d '{"auth_token":"<KILL_SWITCH_RESET_TOKEN>","reset_by":"<you>"}'
  ```
  Requires the `KILL_SWITCH_RESET_TOKEN` env secret — there is **no UI bypass**. Only
  reset after the breach cause is understood (check `risk:*` in Redis, recent fills).

## Deploy a service
- **Canonical prod path:** `./scripts/deploy.sh` — preflight (gcloud/terraform/docker
  authed) → build+push images to Artifact Registry (git-tagged) → terraform apply.
  GitHub Actions only *builds* on push; it does not apply (manual-dispatch only).
- **Local (no GCP):** `./scripts/local_stack.sh` (wedge+control plane) or
  `./scripts/api_plane_stack.sh` (sandbox API plane). Verify with the matching smoke.

## Rollback a service
1. Cloud Run keeps prior revisions: route 100% traffic back to the last-good revision
   (instant, no rebuild). 2. For infra changes, `terraform apply` the previous state.
3. Re-run the relevant smoke + `GET /status` to confirm recovery. 4. If a kill switch
   tripped during the bad deploy, reset it (above) only after confirming green.

## Provider / venue outage (market data or rails)
1. `GET connector-runtime:.../v1/connectors/health` — the `unhealthy[]` list and
   per-venue `status` (degraded/down) name the affected venues.
2. Market-data connectors reconnect with exponential backoff; one bad symbol never
   takes a venue down. risk-engine's exchange-health gate fails *closed* on stale
   `health:latency:*` — so trades on a degraded venue are already blocked.
3. Disable a venue explicitly if needed: `POST /v1/connectors/{venue}/disable`.
4. Africa rails are alert-only (no auto-execution), so a rail outage degrades alerts,
   not money movement.

## Webhook backlog / failed deliveries
1. `GET webhook-service:.../v1/deliveries` — `status: failed` rows are the backlog.
2. Replay a specific event once the receiver recovers:
   `POST /v1/webhooks/endpoints/{id}/replay` `{"event_id": "..."}`.
3. Rotate a leaked signing secret: `POST /v1/webhooks/endpoints/{id}/rotate-secret`
   (the partner must update their verifier).

## Failed settlement
1. `GET settlement-status:.../v1/settlements/{order_id}` — `status` + `history[]` show
   where it stalled (pending/processing/failed). 2. Cross-check the source on/off-ramp
   order. 3. If the partner missed the event, replay it via webhook-service (above).

## Degraded AI / agent response
1. AI is advisory and **fail-open**: if the opportunity-ranker is down, risk-engine
   still gates deterministically (proven by `test_advisory_failopen.py`). No action
   needed for trading safety — only advisory annotations are lost.
2. Governance is **fail-closed**: a version can't be promoted without a passing eval
   (`agent-registry` ↔ `agent-evals`), and sensitive/blocked actions can't auto-execute
   (`approval-gate-service`). Verify with `governance_smoke.sh`.
3. If an agent misbehaves, `disable` its connector / revoke its key; the deterministic
   risk-engine remains the sole final authority regardless.

## Rotate API credentials (partners)
- Partner key: `POST partner-auth:.../v1/partner/keys/{id}/rotate` (old secret stops
  verifying immediately) or `/revoke` to kill it. Secrets are hashed at rest.
- Webhook signing secret: rotate via webhook-service (above).

## Billing / metering outage
- Metering is **fail-open at the gateway** — a metering outage never drops partner
  traffic; usage is best-effort. Reconcile counts from gateway logs after recovery.
- Invoices are generated from `api-metering` usage; re-run invoice generation for the
  affected period once metering is restored (`POST tenant-billing:.../v1/billing/invoices`).

---

## Go-live checklist (docs/11)
- [ ] staging validated; all smokes green (`failover`, `api_plane`, `governance`,
      `paper_trade`, `control_plane`)
- [ ] `status-service` wired to external uptime monitoring (503 = page)
- [ ] critical alerts + incident channel ready; commander rota defined
- [ ] secrets rotated in non-dev; `KILL_SWITCH_RESET_TOKEN` held by on-call only
- [ ] rollback tested (traffic-shift to prior Cloud Run revision)
- [ ] **gated items NOT enabled**: production partner keys (`PARTNER_PROD_KEYS_ENABLED`
      stays false), live rails, autonomous execution (`ENABLE_AUTONOMOUS_EXECUTION`
      stays false) — until licensing + advisory-loop proof clear (docs/REGULATORY_BRIEF.md, docs/10)
