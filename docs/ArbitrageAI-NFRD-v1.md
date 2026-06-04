# ArbitrageAI Platform — Non-Functional Requirements Document (NFRD)

**Classification:** Internal — Engineering  
**Version:** 1.0  
**Date:** June 4, 2026  
**Prepared for:** Technical Lead & Engineering Team

---

## 1. Performance Requirements

### 1.1 Latency

| ID | Requirement | Target | Measurement |
|---|---|---|---|
| PERF-01 | Trade signal-to-order latency | p99 < 500ms | Cloud Monitoring: signal_emit_ts → order_submit_ts |
| PERF-02 | Spread detection latency | < 50ms from WebSocket price update to signal | Cloud Trace |
| PERF-03 | Vertex AI inference latency | < 50ms per prediction | Vertex AI prediction latency metric |
| PERF-04 | Dashboard page load | p95 < 2s web; p95 < 1.5s mobile | Web Vitals (LCP); Firebase Performance SDK |
| PERF-05 | P&L update to dashboard | < 3s from trade close to refresh | Firestore listener propagation time |
| PERF-06 | Exchange connection validation | < 2s from API key paste to confirmation | Backend API response time |
| PERF-07 | WhatsApp notification delivery | < 5s from trade close to message received | Cloud Run dispatcher latency |
| PERF-08 | SMS delivery | < 15s from trigger to received | Twilio delivery timestamp |
| PERF-09 | Subscription activation | < 60s from payment to tier active | Webhook → Firestore update latency |
| PERF-10 | Exchange reconnection | < 30s after WebSocket disconnect | CCXT Pro reconnect timing |

### 1.2 Throughput

| ID | Requirement | Target |
|---|---|---|
| THRU-01 | Concurrent active strategy workers | 1,000,000 |
| THRU-02 | Strategy evaluations per second | 500,000 |
| THRU-03 | Trade records written per day | 50,000,000 |
| THRU-04 | Exchange API calls per minute | 200,000 |
| THRU-05 | Concurrent dashboard WebSocket sessions | 1,000,000 |
| THRU-06 | WhatsApp notifications per second (burst) | 10,000 |
| THRU-07 | Firestore reads per second | 1,000,000 |

---

## 2. Availability & Reliability

### 2.1 Uptime SLOs

| ID | Service | Target SLO | Window |
|---|---|---|---|
| AVAIL-01 | Control plane — Starter/Pro | 99.9% | Rolling 30 days |
| AVAIL-02 | Control plane — Institutional/Enterprise | 99.95% | Rolling 30 days |
| AVAIL-03 | Strategy execution workers | 99.9% | Rolling 30 days |
| AVAIL-04 | Exchange Connector Service | 99.9% | Rolling 30 days |
| AVAIL-05 | Notification dispatcher | 99.5% | Rolling 30 days |
| AVAIL-06 | BigQuery analytics pipeline | 99% | Rolling 30 days |

### 2.2 Recovery Objectives

| ID | Metric | Target |
|---|---|---|
| RECO-01 | RTO — P1 incidents | < 15 minutes |
| RECO-02 | RPO — trade data | < 1 minute (Cloud Spanner synchronous) |
| RECO-03 | RPO — user config | < 5 minutes (Firestore multi-region) |
| RECO-04 | Strategy worker pod restart | < 30s |
| RECO-05 | KILL_SWITCH to all workers paused | < 10s |

### 2.3 Fault Tolerance

| ID | Requirement | Implementation |
|---|---|---|
| FAULT-01 | No SPOF in control plane | GKE Autopilot multi-zone; min 3 replicas |
| FAULT-02 | Exchange connector failover | REST fallback within 5s of WebSocket failure |
| FAULT-03 | Vertex AI fallback | Static allocation model if online prediction unavailable |
| FAULT-04 | Notification channel fallback | WhatsApp failure → SMS within 30s |
| FAULT-05 | FX rate failover | Wise API fallback within 5s |
| FAULT-06 | Database write failure | Cloud Spanner write retried 3x with exponential backoff |

---

## 3. Scalability

| ID | Requirement | Target | Implementation |
|---|---|---|---|
| SCALE-01 | Horizontal pod autoscaling | 0 to 1M concurrent workers | GKE Autopilot HPA on CPU + Pub/Sub queue depth |
| SCALE-02 | Database scaling | 50M trade writes/day | Cloud Spanner linear horizontal scaling |
| SCALE-03 | User registration | 10M registered users | Firebase Auth (100M+ capacity) |
| SCALE-04 | Zero-cost idle users | Near-$0 for inactive users | GKE Autopilot scale-to-zero |
| SCALE-05 | Africa traffic spikes | 10x spike during naira devaluation events | GKE Autopilot auto-provision; Cloud CDN edge cache |
| SCALE-06 | BigQuery analytics | Sub-10s P&L queries | Partitioned by user_id + date; clustered by strategy_type |

---

## 4. Security

### 4.1 Data Security

| ID | Requirement | Implementation |
|---|---|---|
| SEC-01 | Credentials encrypted at rest | GCP Secret Manager AES-256 |
| SEC-02 | Credentials encrypted in transit | TLS 1.3 enforced by Cloud Load Balancer |
| SEC-03 | Credentials never in application database | Secret Manager only; Firestore stores reference paths only |
| SEC-04 | Credentials never logged | Log sanitization; structured log schema excludes credential fields |
| SEC-05 | Credentials never returned to frontend | API responses never include raw API keys |
| SEC-06 | User data encrypted at rest | Google-managed encryption (Firestore + Cloud Spanner) |
| SEC-07 | PII isolation | User PII separate from trade data in different Firestore collections |

### 4.2 Access Control

| ID | Requirement | Implementation |
|---|---|---|
| SEC-08 | Workload Identity | All pods use Workload Identity; zero static service account keys |
| SEC-09 | Namespace isolation | GKE NetworkPolicy prevents cross-namespace communication |
| SEC-10 | Secret Manager scoping | Each namespace SA can only read its own /users/{uid}/ secrets |
| SEC-11 | API authentication | Firebase Auth JWT on every API endpoint |
| SEC-12 | Admin access | Separate admin role claim in Firebase Auth custom tokens |
| SEC-13 | VPC-SC perimeter (Institutional+) | Data plane cluster in VPC Service Controls perimeter |

### 4.3 Application Security

| ID | Requirement | Implementation |
|---|---|---|
| SEC-14 | Input validation | Zod schema validation (TypeScript); server-side on all APIs |
| SEC-15 | WAF protection | Cloud Armor: OWASP Top 10 ruleset |
| SEC-16 | Rate limiting | Per-IP: Cloud Armor; per-user: API middleware |
| SEC-17 | SSRF protection | Exchange Connector only connects to whitelisted domains |
| SEC-18 | Dependency scanning | GitHub Dependabot + Snyk in CI/CD; no deploy with critical CVEs |
| SEC-19 | Secrets scanning | GitHub secret scanning; pre-commit hook blocks credential commits |
| SEC-20 | Penetration testing | Annual third-party pentest; required before Institutional launch |

---

## 5. Compliance

| ID | Requirement | Jurisdiction | Timeline |
|---|---|---|---|
| COMP-01 | FSCA CASP registration | South Africa | Before ZA public launch |
| COMP-02 | SEC Nigeria Digital Asset registration | Nigeria | Before NG public launch |
| COMP-03 | Kenya VASP Act registration | Kenya | Before KE public launch |
| COMP-04 | GDPR compliance | EU | Before EU user acceptance |
| COMP-05 | POPIA compliance | South Africa | Before ZA public launch |
| COMP-06 | NDPA compliance | Nigeria | Before NG public launch |
| COMP-07 | FinCEN MSB legal opinion | USA | Before US marketing launch |
| COMP-08 | SOC 2 Type II | Global (Institutional) | Month 12 |
| COMP-09 | SARS-compliant tax export | South Africa | Phase 3 |
| COMP-10 | Data retention policy | Global | 12-month active; 7-year archived |
| COMP-11 | Non-custodial legal opinion | Global | Before any launch |
| COMP-12 | Nigeria P2P fiat leg legal review | Nigeria | Before automating fiat leg (Phase 5) |

---

## 6. Observability

| ID | Requirement | Implementation |
|---|---|---|
| OBS-01 | Distributed tracing | Cloud Trace via OpenTelemetry SDK |
| OBS-02 | Structured logging | JSON logs to Cloud Logging from all services |
| OBS-03 | Custom metrics | Trade execution latency, signal latency, execution quality score |
| OBS-04 | SLO dashboards | Cloud Monitoring SLO tracking |
| OBS-05 | Error tracking | Sentry (frontend + backend) |
| OBS-06 | Product analytics | PostHog (onboarding funnel, retention cohorts) |
| OBS-07 | Alerting | PagerDuty: P1/P2 (24/7 on-call); Slack: P3/P4 |
| OBS-08 | Audit logging | All admin actions + secret access attempts; immutable |

---

## 7. Maintainability

| ID | Requirement | Implementation |
|---|---|---|
| MAINT-01 | Infrastructure as Code | All GCP resources in Terraform; no manual provisioning |
| MAINT-02 | Kubernetes workload management | All GKE workloads in Helm charts |
| MAINT-03 | CI/CD pipeline | GitHub Actions → Cloud Build → GKE rolling deploy |
| MAINT-04 | Zero-downtime deploys | GKE rolling updates with health checks + PodDisruptionBudget |
| MAINT-05 | Feature flags | LaunchDarkly or GCP Remote Config |
| MAINT-06 | Database migrations | Flyway for Cloud Spanner DDL; backward-compatible only |
| MAINT-07 | Code quality | ESLint + Prettier (TypeScript); Ruff (Python); enforced in CI |
| MAINT-08 | Test coverage | Min 80% unit test coverage; strategy logic requires 95% |
| MAINT-09 | Documentation | All APIs in OpenAPI 3.0; all services have README + runbook |

---

## 8. Usability & Accessibility

| ID | Requirement | Target |
|---|---|---|
| UX-01 | Onboarding completion rate | > 85% of signups activate AI engine |
| UX-02 | Exchange connection time | < 4 minutes wizard start to validated connection |
| UX-03 | Time to first trade | < 24 hours from AI engine activation |
| UX-04 | Mobile-first design | All screens functional at 375px; tested on Android Go |
| UX-05 | Low-bandwidth mode | Core dashboard on 3G (< 1MB page weight) |
| UX-06 | Offline state | Cached P&L shown with "last updated" indicator |
| UX-07 | Accessibility | WCAG 2.1 AA; VoiceOver/TalkBack support |
| UX-08 | Localization | English launch; French Phase 2; Portuguese Phase 3 |
| UX-09 | Zero jargon rule | No technical trading terms without plain-language explanation |

---

## 9. Africa-Specific Non-Functional Requirements

| ID | Requirement | Target |
|---|---|---|
| AFR-01 | GCP region for South Africa | africa-south1 (Johannesburg); < 50ms to VALR/Luno |
| AFR-02 | Nigeria/West Africa routing | europe-west1 until GCP Lagos available |
| AFR-03 | WhatsApp primary notification | 100% of NG/KE/GH/TZ/UG/RW users; SMS fallback within 30s |
| AFR-04 | Mobile money payment success | > 95% successful confirmations |
| AFR-05 | NGN exchange rate freshness | P2P rate updated every 5 minutes |
| AFR-06 | Low-end Android performance | Usable on Android 9+ with 2GB RAM |
| AFR-07 | Africa P2P data availability | Historical data collector starts Day 1 of build |
| AFR-08 | SARS tax export | Available by Phase 3 |

---

## 10. Disaster Recovery

| ID | Scenario | Response Plan | RTO | RPO |
|---|---|---|---|---|
| DR-01 | africa-south1 outage | Failover to europe-west4 | < 5 min | < 1 min |
| DR-02 | Cloud Spanner regional outage | Multi-region auto-failover | < 1 min | 0 |
| DR-03 | Vertex AI endpoint down | Static allocation model activates | < 1 min | N/A |
| DR-04 | Exchange API outage (Binance) | Binance strategies paused; others continue | < 5 min | N/A |
| DR-05 | Stripe outage | Flutterwave backup; new subs queued | < 30 min | N/A |
| DR-06 | Complete platform outage | KILL_SWITCH; status page; SMS all users | < 15 min | < 1 min |
| DR-07 | Security breach | Affected secrets rotated; users notified; legal engaged | < 30 min | N/A |

---

## 11. Cost Management

| ID | Requirement | Implementation |
|---|---|---|
| COST-01 | GKE scale-to-zero for inactive users | Worker pods terminated when engine inactive |
| COST-02 | BigQuery cost control | Query cost estimates enforced before dashboard queries |
| COST-03 | Vertex AI cost monitoring | Budget alerts at 80% of monthly budget |
| COST-04 | Notification cost monitoring | Alert if WhatsApp cost/user > $0.05/day |
| COST-05 | GCP budget alerts | Alerts at 50%, 80%, 100% of monthly budget |
| COST-06 | Cost per user tracking | Monthly cost tracked per tier; alert if margin < 70% |

---

*NFRD v1.0 — June 4, 2026. All requirements subject to review by Tech Lead prior to Phase 1 kickoff.*
