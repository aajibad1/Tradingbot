# Master strategy — canonical index + coverage

Consolidates the (many) strategy briefs into one source of truth, mapped to what's
actually in this repo. Companion docs: `PLATFORM_ARCHITECTURE.md` (services),
`CONNECTOR_ARCHITECTURE.md` (pluggable connectors), `REGULATORY_BRIEF.md` (custody/
licensing), `claude-code-starter-doc-clerk-ai.md` (the full brief).

## Vision

A **dual-business, shared-core, event-driven GCP platform**:
1. **Hybrid AI-assisted market-movement trading** (the wedge) — deterministic
   signals + AI ranking/debate + a hard deterministic risk core.
2. **Africa crypto/stablecoin orchestration API** ("Plaid for crypto in Africa")
   — *gated on the trading wedge proving out + money-transmission licensing.*

One owned core (connectors, normalization, compliance/risk, FX/corridor, analytics)
serves both; data compounds across both (the flywheel + moat).

## Build order vs. repo status

| # | Brief build step | Repo status |
|---|---|---|
| 1 | Shared core (connectors, events, auth, db, observability) | ✅ connector contract, Pub/Sub, Cloud SQL, Redis, BQ |
| 2 | Control plane (auth, onboarding, compliance, billing) | ✅ core-api (Clerk-ready), onboarding+KYC+disclosures+policy, Stripe, RBAC, audit |
| 3 | Ingestion + signal layer | ✅ market-data (CCXT), fx-rate, **signal-engine**; ❌ movement-feature-builder, live Africa rails/on-chain |
| 4 | Deterministic decision + paper trading | ✅ opportunity-engine, **risk-engine** (kill switch, drawdown, eligibility, **directional budget**), paper-trader; 🟡 regime-classifier |
| 5 | Africa API plane (gateway, portal, on/off-ramp, metering) | ❌ not started — **gated on wedge + licensing** |
| 6 | Multi-agent intelligence layer | 🟡 **partially real**: opportunity-ranker (advisory), debate-service (proposer→skeptic→judge), corridor-intelligence (Perplexity), ai-ops-agent (Claude); ❌ orchestrator/registry/evals/memory services |
| 7 | Live execution + route optimizer + advanced AI | 🟡 execution-orchestrator stub; ❌ route-optimizer, live |

**Trading MVP ≈ 80–90% built. API MVP ≈ 0% (by design).**

## Multi-agent layer (provider split, already partly built)

- **Perplexity** = cited market/finance/corridor research (sentiment-service,
  corridor-intelligence). **Claude** = reasoning/synthesis/debate (ai-ops-agent,
  debate-service). Both **advisory-only**; the risk-engine is the sole final
  authority; every output carries `model_version` + `sources` for audit.
- Net-new if/when scaled: `agent-orchestrator`, `agent-registry`, `agent-evals`,
  scoped `agent-memory`, `approval-gate`. Build these only when the advisory loop
  is proven and live — not before.

## Priority order (the brief's own, which we follow)

1. **Trust & reliability** — controls exist; **prove them**: deploy, status/health
   aggregation, SLOs, tested failover.
2. **Narrow wedge** — pick ONE (funding-carry for global traders OR one Africa
   corridor) and dominate before broadening.
3. Ease of use → 4. Proprietary data (needs users) → 5. Distribution → 6. AI depth.

## The standing guidance (consistent across every brief)

- **Do NOT** open the Africa API business or full agent mesh before the trading
  wedge is validated + deployed and licensing is scoped. They're large, regulated,
  provider-dependent.
- **DO** finish + deploy the trading MVP, make reliability a provable feature, and
  sharpen one wedge. That — not more services — is the path to world-class.
