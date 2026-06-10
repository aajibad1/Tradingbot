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

## Expansion task
Claude should implement the agent control plane, audit trail, eval harness, and approval gating consistent with this document.
