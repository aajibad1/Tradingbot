# Delivery Status
Updated: 2026-09-17 (cycle 2)

## Context

This repo is being operated under `africa_treasury_os_autonomous_engineering_team.md`
(the "operating contract") as of this cycle. The contract's product description —
supplier payments, multi-route FX comparison, compliant settlement, approval
workflows, double-entry ledger, reconciliation, multi-tenant API/dashboard for
African businesses — maps onto traditbot's **existing Africa API-plane**, not a
greenfield build. Per the contract's own §5.1 ("use the repository's existing
standards if present"), this repo's standards are Python 3.12 / FastAPI /
Pub/Sub / Cloud SQL Postgres / BigQuery / Terraform — the TypeScript/Next.js/
NestJS stack in the contract's baseline table is the greenfield fallback and
does **not** apply here.

Africa-plane service ↔ contract-role mapping:

| Contract concept | traditbot service |
|---|---|
| Supplier payment / payout orchestration | `offramp-orchestrator` |
| Local collection (NGN) | `onramp-orchestrator` |
| Multi-route FX/rail comparison | `routing-service`, `fx-rate-service`, `corridor-engine` |
| Route confidence / corridor intelligence | `corridor-intelligence-service`, `debate-service` |
| Double-entry ledger | `accounts-service` |
| Per-tenant wallets / aggregated balances | `wallet-service` |
| Settlement read-model / reconciliation | `settlement-status` |
| Signed partner webhooks | `webhook-service` |
| Tenant-scoped API keys | `partner-auth` |
| Usage metering / quotas | `api-metering` |
| Billing/invoicing | `tenant-billing` |
| Developer portal / sandbox playground | `developer-portal` |
| Ops console (payment lookup, exceptions, provider health) | `admin-console` |
| Identity/tenancy/RBAC/audit/onboarding | `core-api` |
| AI advisory (never authoritative) | `opportunity-ranker`, `ai-ops-agent`, `debate-service` (governance: `approval-gate-service`, `agent-registry`, `agent-evals`) |

## Current milestone

Mapped against the contract's milestones, using **existing** exit criteria as
the yardstick (not re-litigating what's already built):

- **Milestone 0 (Foundation/Guardrails): substantially met.** CI (`ci.yml`) runs
  ruff, mypy, pytest w/ coverage gates (50% per-service, 80% shared), bandit,
  terraform fmt/validate, docker build smoke, frontend typecheck+build. No
  ADRs existed before this cycle (see `docs/adr/`, added this cycle). No
  CODEOWNERS or issue/PR templates existed before this cycle.
- **Milestone 1 (Identity/Tenancy/Audit/Domain core): met.** `core-api` has
  org/user/RBAC/onboarding; `shared/tenant.py` provides tenant-scoping
  primitives; `AuditLogEntry` (shared model, this session) is now the
  canonical audit-event contract, validated end-to-end for the two real
  producers (`ai-ops-agent`, `approval-gate-service`).
- **Milestone 2 (Quotes/Routing/Approvals): partially met.** `routing-service`
  + `fx-rate-service` + `corridor-engine` give provider-neutral, explainable
  route scoring (cost/settlement-estimate/reliability). **Gap:** no
  multi-step human approval workflow or segregation-of-duties policy for
  *payment* actions specifically (the AI governance approval gate exists for
  *agent* actions, not for payment approval) — see Backlog below.
- **Milestone 3 (Ledger/Lifecycle): partially met.** `accounts-service` is a
  real, tested, Decimal-backed (`Numeric(38,8)`), balance-asserting
  double-entry ledger (`ledger.py`) — this is exactly what the contract's
  §6.2 ledger invariants require. **Gap:** `wallet-service` (the tenant-facing
  balance API) uses Python `float` for balances and is explicitly documented
  in its own module docstring as a sandbox simplification ("production uses
  Decimal") — not yet wired to `accounts-service`'s ledger. See Backlog.
- **Milestone 4 (Provider execution/Reconciliation): met for the sandbox
  scope.** Signed webhook ingestion + verification (`webhook-service/signing.py`,
  HMAC-SHA256 + timestamp), retry/replay (`/v1/webhooks/endpoints/{id}/replay`),
  `settlement-status` projects funding.*/payout.* into one normalized record.
  Proven live this session (`scripts/settlement_flow_smoke.sh`) using a real
  producer-constructed envelope, not a hand-built test fixture.
- **Milestone 5 (UX/Ops readiness): partial.** `developer-portal` +
  `admin-console` exist with A2A mesh visibility; no accessibility/load test
  suite found for these specifically; runbooks exist (`docs/RUNBOOKS.md`,
  `docs/11-runbooks-go-live.md`).

## Completed this cycle

- Inspected repository architecture, CI, tests, docs, security posture
  against the contract (see Quality dashboard below).
- Added `docs/adr/` with the 8 contract-mandated architectural decisions,
  documenting decisions already de facto made in this codebase (see ADR
  index).
- Added `.github/CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md`,
  `.github/ISSUE_TEMPLATE/` (contract §9.1/§10.2 formats), plus the
  contract's full label taxonomy (`type:*`, `area:*`, `priority:*`,
  `risk:*`, `status:*`) and 6 GitHub milestones (M0–M5) on the real repo.
- Filed 3 real GitHub issues for the gaps found this cycle:
  [#20](https://github.com/aajibad1/Tradingbot/issues/20) wallet-service
  float→Decimal (P1, risk:financial),
  [#21](https://github.com/aajibad1/Tradingbot/issues/21) payment approval
  workflow w/ segregation-of-duties (P1),
  [#22](https://github.com/aajibad1/Tradingbot/issues/22) KYC/KYB +
  sanctions/PEP screening sandbox adapters (P1, risk:regulatory).
- Implemented #20: `wallet-service` migrated float→Decimal, wire balances
  as strings, JSON-float input rejected at the request boundary, 3 new
  regression tests, verified live (`0.1+0.1+0.1 == "0.30000000"` exactly).
  Opened [PR #23](https://github.com/aajibad1/Tradingbot/pull/23); sent
  for independent review per contract §10.3 (ledger change: independent
  reviewer required, no self-approval) before merge.
- Added this file (`docs/STATUS.md`) as the contract's standing status
  report.

## In progress

- PR #23 (wallet-service Decimal migration) — awaiting independent review
  before merge.
- Next up after #23 merges: issue #22 (KYC/KYB + screening sandbox
  adapters) — highest-priority remaining unblocked P1, no dependency on
  #21 or #23.

## Blocked

- **KYC/KYB and sanctions/PEP screening**: `docs/09-security-compliance.md`
  lists these as required compliance controls, but no service implements
  even a sandbox simulation of them yet (contract §7.4 requires a
  "KYC/KYB status simulation" and "sanctions/compliance screening
  simulation" adapter at minimum). No legal/partner decision has been made
  per this repo's own conventions — `docs/REGULATORY_BRIEF.md` exists for
  exactly this reason. Not fabricating compliance status; flagging as an
  open backlog item pending a real decision, per contract §12's hard rule
  against research agents producing compliance claims.
- **Real-money/custody/lending/live-partner execution**: correctly not
  enabled anywhere in this repo (`live_enabled=false`, PAPER-only execution
  posture, SANDBOX-only API plane) — consistent with the contract's §1.1
  boundary #7 and this session's explicit instruction not to enable any of
  this.

## Quality dashboard

- Build: docker build smoke configured per-service in CI; not run this cycle
  (no Dockerfile changes).
- Lint: `ruff check services/ shared/` — clean (verified this cycle).
- Type check: `mypy` on `shared/` — clean, 37 files (verified this cycle).
  Per-service mypy is advisory (`continue-on-error`) per CI config; several
  pre-existing advisory findings not touched this cycle.
- Unit tests: all 40 services + `shared/tests` green (142 tests in
  `.claude/verify`'s `shared/tests` slice; full per-service sweep green,
  verified this cycle).
- Integration tests: `scripts/governance_smoke.sh`, `scripts/signal_plane_smoke.sh`,
  `scripts/settlement_flow_smoke.sh`, `scripts/paper_trade_local.sh` all
  green as of this cycle (settlement_flow_smoke added this session).
- Security scans: `bandit -r services/ shared/ -ll -ii` — clean (verified
  this cycle, matches CI's exact invocation).
- Deployment: not exercised this cycle (no deployed GCP project available to
  this session).

## Risks

- **Financial integrity**: `wallet-service` float-based balances are a real
  gap relative to the contract's §6.1 money-representation invariant. Low
  *current* risk (SANDBOX-only, single-instance, in-memory — no real funds),
  but must be closed before any live-money milestone. Tracked in Backlog.
- **Security/privacy**: none new identified this cycle beyond what
  `docs/09-security-compliance.md` already tracks as pre-go-live gates.
- **Compliance/partner**: KYC/KYB/screening simulation gap (see Blocked).
  No regulatory or partner claims have been made; `docs/REGULATORY_BRIEF.md`
  remains the source of truth for counsel-facing questions.
- **Reliability**: none new identified this cycle.

## Next prioritized work

1. File real GitHub issues for the two P1 gaps: wallet-service Decimal/ledger
   migration; payment-approval (not agent-approval) workflow with
   segregation-of-duties.
2. Implement a KYC/KYB status-simulation adapter (contract §7.4) — sandbox
   only, matching the existing adapter-simulation conventions already used
   throughout this repo (corridor-engine, onramp/offramp providers).
3. Wire `wallet-service` balance mutations through `accounts-service`'s
   ledger (or promote `accounts-service` to be the sole balance authority
   and make `wallet-service` a read/aggregation view over it) instead of
   maintaining two independent balance representations.
