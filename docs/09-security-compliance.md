# 09 Security and Compliance

## Purpose
Defines the mandatory security and compliance controls required before onboarding real users.

## Security controls
- RBAC and tenant isolation
- MFA for admins and sensitive actions
- Secret Manager usage for all credentials
- API key hashing and rotation
- webhook signing and verification
- least-privilege IAM
- encryption at rest and in transit
- audit logging for admin and policy actions
- abuse detection and rate limiting
- session revocation and device visibility

## Compliance controls
- KYC workflow
- KYB workflow
- sanctions screening
- PEP screening
- transaction monitoring
- manual review queue
- suspicious activity escalation workflow
- retention and evidence policy
- policy documentation and approvals

## Readiness gates
Before go-live:
- security review completed
- RBAC tested
- secrets rotated in non-dev environments
- audit trail validated
- compliance review queue operational
- onboarding disclosures approved
- support escalation path defined

## Expansion task
Claude should expand this into implementation controls, policy checklists, access matrices, and review workflows.
