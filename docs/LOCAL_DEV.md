# Running the platform locally (no GCP)

Everything runs without cloud: SQLite for the control plane, local Redis for risk
state, `NullPublisher` (or a Pub/Sub **emulator**) for events. Three levels, by scope.

## 1. Whole stack, kept up for interactive testing

```bash
./scripts/local_stack.sh        # needs a reachable Redis (redis-server / docker)
```
Boots the control plane (core-api + accounts-service on SQLite), the execution
wedge (risk-engine + paper-trader + opportunity-engine + trade-ledger), and the
advisory/analytics services (opportunity-ranker, route-optimizer). Brings up
`status-service` **last**, pointed at the mesh it just started, so the aggregated
views are real:

- `GET  http://127.0.0.1:8087/status`        — live health of every local service
- `GET  http://127.0.0.1:8087/slo/catalog`   — documented SLO targets
- `POST http://127.0.0.1:8087/slo/evaluate`  — error-budget + burn-rate verdict (503 on PAGE)
- `GET  http://127.0.0.1:8084/ml-export/{decisions,funnel,advisory-scorecard,route-quality}`
- `POST http://127.0.0.1:8086/rank`          — route ranking (+ optional realized calibration)

Cross-service messaging here uses `NullPublisher` (logs instead of delivering);
the smokes below drive the flow over HTTP.

## 2. Targeted end-to-end smokes

```bash
./scripts/control_plane_smoke.sh   # onboarding → billing → funding → live-enable → dashboard
./scripts/paper_trade_local.sh     # risk-engine + paper-trader; proves the kill-switch backstop
```
Both boot the services they need and assert the real flow. No cloud, no mocks at
the cross-service seam.

## 3. Real event flow via the Pub/Sub emulator

```bash
./scripts/local_stack_pubsub.sh    # needs Redis + an emulator backend (see below)
```
Runs the **genuine** event-driven path — same as cloud, pointed at the emulator:

```
publish Opportunity → arb-opportunities
  → risk-engine gates        → arb-approved + arb-risk-decisions
    → paper-trader simulates  → arb-trade-fills
      → trade-ledger sinks    (local-sink: rows logged, no BigQuery)
```

**How the decoupling works.** Pub/Sub is separated from "real GCP" in
`shared/pubsub/publisher.py:pubsub_project_id()`:

| Env | Effect |
|---|---|
| `GCP_PROJECT_ID` set | real GCP: real Pub/Sub **and** BigQuery + Secret Manager |
| `PUBSUB_EMULATOR_HOST` set, `GCP_PROJECT_ID` **unset** | Pub/Sub → emulator; BigQuery/Secret-Manager stay in local mode |
| neither | `NullPublisher` (level 1/2 above) |

So the emulator carries the real event flow while `trade-ledger` writes to a
**local-sink** (logged rows, no BigQuery) and `market-data` keeps using
`LOCAL_SECRET_*` env vars. Topics + subscriptions are created by
`scripts/pubsub_emulator_bootstrap.py` (mirrors the terraform `subscriptions` map).

**Emulator backend.** The runner auto-selects:
- **gcloud** `beta emulators pubsub` — used if a JRE is on PATH (the emulator is a
  Java app). `gcloud` is installed here but **no JRE is present**, so this path is
  currently unavailable on this machine until a JRE is installed
  (`brew install temurin`).
- **Docker** fallback — `EMULATOR_IMAGE` (default `google/cloud-sdk:emulators`).
  Requires pulling the image once (`docker pull google/cloud-sdk:emulators`).

### Status of the emulator path (2026-06-10)
- Code: **complete and unit-tested** — the Pub/Sub-vs-GCP decoupling
  (`pubsub_project_id`/`pubsub_enabled`), every wedge service's subscriber gate,
  and the trade-ledger local-sink. Suites green (shared 18, risk-engine 70,
  paper-trader 21, opportunity-engine 26, trade-ledger 52).
- Scripts: `local_stack_pubsub.sh` + `pubsub_emulator_bootstrap.py` written and
  syntax-/lint-clean.
- **Live end-to-end run not yet executed** — needs either a JRE (for the gcloud
  emulator) or the Docker emulator image pulled. The image pull was deferred.

## Cloud deploy
Unchanged and still gated only on **owner / projectIamAdmin credentials**
(`aajibad10` is editor). Local testing does not need this. See `scripts/deploy.sh`.
