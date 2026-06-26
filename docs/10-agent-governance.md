# 10 Agent Governance

## Purpose
Defines how the multi-agent system behaves safely in production.

## Agent roles
- orchestrator
- Claude logic agent
- market agent
- finance agent
- critic/debate agent
- execution guard agent

## Governance principles
- no unrestricted autonomous execution
- explicit tool permissions per agent
- scoped memory only
- human approval for sensitive actions
- prompt and model version tracking
- output traceability
- replayability for major decisions

## Required controls
- record model version used
- record prompt version used
- store tool calls and outputs
- attach evidence to recommendations
- store dissenting views when debate is used
- require policy pass before live action
- allow human override with audit trail

## Evaluation requirements
- accuracy review
- latency review
- hallucination review
- post-outcome analysis against realized results
- red-team prompts and prompt injection tests

## Implemented
- **Approval gating + permission model** — `services/approval-gate-service`: classifies
  every proposed agent action (read→auto, sensitive→human approval, withdrawals/
  leverage→hard-blocked), records model_version/prompt_version/evidence, and emits an
  audit event per proposal + decision. It is a safety control, never an executor.
- **Deterministic policy enforcement** — `services/risk-engine` (the sole final authority).
- **Agent + prompt/model versioning** — `services/agent-registry`: immutable prompt
  versions; a version cannot be activated until it has a passing eval verdict.
- **Eval harness + promotion gate** — `services/agent-evals`: accuracy/hallucination/
  latency thresholds; the registry consults its verdict before activation.
- **Agent↔agent interop (A2A)** — `shared/a2a`: the Agent2Agent contract (the
  complement to MCP's agent↔tool contract). Each agent publishes an Agent Card at
  `/.well-known/agent-card.json` advertising its skills, and exposes a JSON-RPC 2.0
  endpoint (`message/send`, `tasks/get`, `tasks/cancel`) via `install_a2a(app, card,
  handler)`. Wired on the critic/debate (`debate-service`), execution-guard
  (`approval-gate-service`), registry, evals, and AI-ops (`ai-ops-agent`) agents.
  Governance invariants are transport-independent: A2A is gated by the same
  permission model — `ai-ops-agent`'s NEVER-tier tools are neither advertised as
  skills nor invocable, and withdrawals/leverage stay hard-blocked. Handlers are
  fail-soft (errors become JSON-RPC errors, never a crash). Peers resolve each
  other via the registry (`client_for(name)`; `A2A_<NAME>_URL` override).

Still to build (gated — only once the advisory loop is proven live): the autonomous
agent orchestrator and scoped agent-memory.

## Expansion task
Claude should implement the agent control plane, audit trail, eval harness, and approval gating consistent with this document.
