# 11 Runbooks and Go-Live

## Purpose
Defines the operational procedures needed to run the platform in production and support onboarding.

## Required runbooks
- deploy service
- rollback service
- rotate API credentials
- provider outage response
- failed settlement response
- webhook backlog response
- KYC provider outage response
- degraded AI/agent response
- billing outage response
- incident severity triage

## Go-live checklist
- staging environment validated
- smoke tests passing
- critical alerts wired
- dashboards operational
- incident channels ready
- support staff trained
- onboarding docs published
- billing and invoicing validated
- sandbox to production path validated
- rollback tested

## Launch phases
1. internal alpha
2. trusted design partners
3. limited beta
4. controlled production
5. scaled onboarding

## Implemented
Concrete, repo-grounded runbooks (every command/endpoint is real) live in
**`docs/RUNBOOKS.md`**: incident triage, kill-switch reset, deploy/rollback, provider/
venue outage, webhook backlog, failed settlement, degraded AI/agent, credential
rotation, billing/metering outage, plus the go-live checklist with the gated-items
guardrail.

## Expansion task
Claude should convert this into detailed runbooks, checklists, ownership tables, and cutover steps.
