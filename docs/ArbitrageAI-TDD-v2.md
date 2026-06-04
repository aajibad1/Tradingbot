# ArbitrageAI Platform — Technical Design Document v2.0

**Classification:** Internal — Engineering  
**Version:** 2.0  
**Date:** June 4, 2026  
**Prepared for:** Technical Lead & Engineering Team  
**Platform:** Google Cloud Platform (GCP)  
**Target Scale:** Millions of users globally — Primary markets: USA, Nigeria, South Africa, Kenya

---

## Executive Summary

ArbitrageAI is a subscription-based, AI-powered cryptocurrency arbitrage SaaS platform built for non-technical retail users. The platform abstracts all exchange connectivity complexity behind guided onboarding flows, executes multi-strategy arbitrage automatically, and delivers real-time P&L in the user's local currency. Built on GCP for global scale, multi-tenancy, and sub-100ms execution latency.

**Core Design Principles:**
1. Zero API knowledge required — fully guided exchange connectivity via OAuth or step-by-step wizard
2. Always-profitable posture — multi-strategy AI portfolio, never single-strategy dependence; strategies validated via backtesting before any live deployment
3. GCP-native — Autopilot GKE, Cloud Spanner, Vertex AI, Secret Manager, africa-south1 region
4. Africa-first — ZAR, NGN, KES currency support; Flutterwave/Paystack/M-Pesa payment rails; WhatsApp-primary notifications
5. Subscription SaaS — Stripe + Flutterwave managed tiers with automated GKE resource allocation per tier
6. Non-custodial — user funds never leave their exchange accounts; platform only executes trades via scoped API keys

---

## 1. System Architecture

### 1.1 GCP Services Reference

| Service | Role | Configuration |
|---|---|---|
| GKE Autopilot | All workloads | Multi-region: us-central1, europe-west4, asia-east1, africa-south1 |
| Cloud Load Balancer | Global HTTPS/WSS ingress | Anycast IP, SSL termination, HTTP/2 |
| Cloud Armor | WAF + DDoS | OWASP ruleset, geo rate-limiting |
| Firebase Auth | User authentication | Email, Google, Apple SSO; scales to 100M+ users |
| Firestore | User profiles, configs, subscription state | Multi-region, real-time listeners |
| Cloud Spanner | Immutable trade ledger + FX rates | nam-eur-asia1 multi-region; ACID compliant |
| BigQuery | P&L analytics, backtest engine, model training | Partitioned by user_id + date |
| Cloud Pub/Sub | Per-user strategy event streaming | Push to worker pods, dead-letter queues |
| Secret Manager | Exchange API keys, OAuth tokens, KILL_SWITCH | Per-user paths, Workload Identity access |
| Vertex AI | Signal models, allocation models, retraining | Custom TensorFlow; AutoML Tabular; weekly pipelines |
| Memorystore (Redis) | Session cache, rate-limit counters, P&L cache | Regional per cluster |
| Cloud CDN | Static assets, low-bandwidth mode (Africa 3G) | Compressed assets for africa-south1 edge |
| Cloud Run | Notification dispatcher, Stripe/Flutterwave webhooks | Serverless, event-driven |
| Cloud Monitoring | SRE observability, SLO tracking | OpenTelemetry SDK, Grafana, PagerDuty |

### 1.2 Multi-Tenancy Model

- **Starter/Pro**: Shared GKE worker pool; isolated per-user GKE namespace (ns-{uid}); NetworkPolicy enforced
- **Institutional**: Dedicated GKE node pool, dedicated Memorystore, VPC-SC perimeter
- **Enterprise**: Dedicated GCP project, custom domain, SLA 99.95%

Secret isolation paths:
- /users/{uid}/exchanges/binance/api_key
- /users/{uid}/exchanges/valr/oauth_token
- /platform/kill_switch

### 1.3 Scaling Targets

| Metric | Target |
|---|---|
| Registered users | 10M |
| Concurrent active traders | 1M |
| Strategy evaluations/sec | 500K |
| Trade records/day | 50M |
| Exchange API calls/min | 200K |

---

## 2. Arbitrage Strategy Engine

### 2.1 Strategy Portfolio

| Strategy | Target APR | Risk Level | Market |
|---|---|---|---|
| Funding Rate Arb | 15-50% APR | Low | Global, all tiers |
| Spatial / CEX-CEX | 0.5-3% per trade | Medium | Global Pro+ |
| Africa P2P Premium | 2-6% per trade | Low-Medium | Africa Pro+ |
| Statistical / Pairs Arb | 10-25% APR | Medium | Global Pro+ |
| CEX-DEX | 0.3-2% per trade | Medium-High | Institutional Phase 5 |

### 2.2 Profitability Pre-Filter

Net Profit = Gross Spread - Trading Fees - Withdrawal Fees - Slippage Estimate - Gas (DEX only)

Minimum threshold: Net Profit > 0.15% after all costs. Trades below this threshold are rejected.

### 2.3 Backtesting Framework (Pre-Deployment Gate)

**No strategy goes live without passing backtesting validation.**

Data sources:
- Global OHLCV + orderbook: Tardis.dev (tick-level historical data)
- Funding rates: Binance/Bybit historical funding rate API
- Africa P2P premium: Custom data collector (Cloud Function, runs from Day 1)

Minimum performance gates:
- Sharpe ratio > 1.5
- Maximum drawdown < 10%
- Win rate > 65%
- Minimum 6 months of historical data
- Tested across at least one bear market period

### 2.4 Execution Quality Monitor

After every trade:
- Execution Quality Score = Actual Net Profit / Expected Net Profit
- Score > 0.85: Good execution
- Score 0.60-0.85: Adjust slippage estimate for this exchange pair
- Score < 0.60: Suspend exchange pair 1 hour, alert engineering, retrain slippage model

### 2.5 Risk Management

| Control | Starter | Pro | Institutional |
|---|---|---|---|
| Max capital per trade | 5% | 15% | 25% |
| Daily loss limit (auto-pause) | -2% | -3% | -5% |
| Max exchange concentration | 40% | 50% | 60% |
| Slippage guard | >0.5% reject | >0.5% reject | >1% reject |
| Funding rate floor | >10% annualized | >8% | >5% |

### 2.6 Profitability Guarantee Architecture

1. Pre-trade gate: Fee-adjusted spread > 0.15% required
2. Execution guard: Real-time slippage check before order submission
3. Post-trade reconciliation: Execution quality score per trade
4. Monthly P&L review: If user net P&L is negative, automated strategy audit triggered
5. Strategy rotation: 7-day underperformance → automatic deprioritization + user notification

---

## 3. Exchange Connectivity

### 3.1 Supported Exchanges (Launch)

| Exchange | Region | Connection Method |
|---|---|---|
| Binance | Global | Guided wizard |
| Coinbase Advanced | US/Global | OAuth 2.0 |
| Kraken | US/EU | OAuth 2.0 |
| Bybit | Global | Guided wizard |
| OKX | Global/Asia | Guided wizard |
| VALR | South Africa | Guided wizard |
| Luno | Africa/EU | OAuth 2.0 |
| Yellow Card | Africa (20 countries) | Guided wizard |
| Quidax | Nigeria | Guided wizard |

### 3.2 Guided API Key Wizard

1. Deep-link button to exchange API management page
2. Screenshot overlay with step annotations
3. Pre-filled API key name: ArbitrageAI-{display_name}
4. Explicit permission guide: Enable Read + Spot Trading; Disable Withdrawals
5. Instant validation: balance check within 2 seconds of paste
6. Human-readable error messages

---

## 4. FX Rate Handling

| Context | Refresh Rate | Storage |
|---|---|---|
| Dashboard P&L display | Every 60 seconds | Memorystore (Redis) |
| Trade execution FX capture | At exact execution timestamp | Cloud Spanner (per-trade, immutable) |
| Tax report FX rate | Rate at time of trade | Cloud Spanner fx_rate_at_execution field |
| Africa P2P premium calculation | Every 5 minutes | Memorystore |

Nigeria special: Three-rate display — CBN official, Wise parallel, P2P rate.

---

## 5. Subscription & Billing

### 5.1 Tier Structure

| Tier | USD/mo | ZAR/mo | NGN/mo |
|---|---|---|---|
| Free Trial | $0 (14 days) | — | — |
| Starter | $9 | ~R165 | ~N13,500 |
| Pro | $29 | ~R530 | ~N43,500 |
| Institutional | $399 | ~R7,300 | ~N598,500 |
| Enterprise | Custom | Custom | Custom |

### 5.2 Africa Payment Rails

- Nigeria: Flutterwave (bank transfer, USSD, mobile money)
- South Africa: Paystack (EFT, card)
- Kenya/East Africa: M-Pesa via Flutterwave
- Global fallback: Stripe

---

## 6. Notification Architecture (WhatsApp-First)

### 6.1 Channel Routing

- Africa (NG, KE, GH, TZ, UG, RW): WhatsApp Business API → SMS fallback → Push
- South Africa: WhatsApp → Telegram → Push → SMS
- Global crypto users: Telegram → Push → Email
- General global: Push → Email → Telegram
- Institutional/Enterprise: Slack webhook → Email → Push

### 6.2 Notification Services

| Channel | Provider | Cost |
|---|---|---|
| WhatsApp Business | Twilio / 360dialog | ~$0.005-$0.015/conversation/day |
| Telegram Bot | Telegram Bot API | Free |
| SMS | Twilio | ~$0.0075/message |
| Push (mobile) | Firebase Cloud Messaging | Free |
| Email | SendGrid | ~$0.001/email |
| Slack webhook | Slack API | Free (Institutional only) |

### 6.3 Telegram Two-Way Commands

- /status — current engine status
- /pnl — today's and MTD P&L
- /pause — pause all strategies
- /resume — resume strategies
- /balance — connected exchange balances

---

## 7. Frontend Architecture

| Layer | Technology |
|---|---|
| Web App | Next.js 15, TypeScript, Tailwind CSS, shadcn/ui |
| Mobile | React Native + Expo (iOS + Android) |
| State | Zustand + TanStack Query |
| Real-time | Firestore real-time listeners + WebSocket |
| Charts | Recharts + D3 |
| Analytics | PostHog |
| Error Tracking | Sentry |

---

## 8. Data Architecture

### 8.1 Trade Record Schema (Cloud Spanner)

```sql
CREATE TABLE trades (
  trade_id STRING(36) NOT NULL,
  user_id STRING(36) NOT NULL,
  strategy_type STRING(50) NOT NULL,
  exchange_buy STRING(50) NOT NULL,
  exchange_sell STRING(50) NOT NULL,
  asset STRING(20) NOT NULL,
  quantity FLOAT64 NOT NULL,
  buy_price FLOAT64 NOT NULL,
  sell_price FLOAT64 NOT NULL,
  gross_spread_pct FLOAT64 NOT NULL,
  fees_total_usd FLOAT64 NOT NULL,
  net_profit_usd FLOAT64 NOT NULL,
  net_profit_local FLOAT64 NOT NULL,
  local_currency STRING(3) NOT NULL,
  fx_rate_at_execution FLOAT64 NOT NULL,
  expected_net_profit_usd FLOAT64 NOT NULL,
  actual_vs_expected_ratio FLOAT64 NOT NULL,
  execution_latency_ms INT64 NOT NULL,
  slippage_actual_pct FLOAT64 NOT NULL,
  slippage_estimated_pct FLOAT64 NOT NULL,
  partial_fill BOOL NOT NULL,
  fill_pct FLOAT64 NOT NULL,
  status STRING(20) NOT NULL,
  created_at TIMESTAMP NOT NULL
) PRIMARY KEY (user_id, created_at DESC, trade_id);
```

---

## 9. Security Architecture

- Cloud Armor: OWASP Top 10, geo rate-limiting, DDoS mitigation
- Workload Identity: All GCP service-to-service auth
- Secret Manager: All credentials; decrypted only in-memory in worker pods
- Withdraw permission enforcement: Wizard explicitly disables withdraw permission
- Key rotation: Automated 90-day rotation prompt
- KILL_SWITCH: Single Cloud Function broadcasts pause to all Pub/Sub subscriptions

---

## 10. Development Roadmap

### Phase 1 — Core Trading Engine (Weeks 1-4)
- CCXT Pro Exchange Connector Service
- Africa P2P premium data collector (Day 1)
- Backtesting framework (BigQuery + Tardis.dev)
- Funding Rate Arbitrage strategy worker
- Profitability pre-filter + execution quality monitor
- Risk Management Service + KILL_SWITCH
- Cloud Spanner trade ledger

Exit gate: 100 live trades with positive net P&L AND backtest Sharpe > 1.5

### Phase 2 — Multi-Tenant Platform (Weeks 5-8)
- Firebase Auth + Firestore user profiles
- GKE namespace isolation
- Stripe + Flutterwave + Paystack + M-Pesa billing
- Guided API Key Wizard (all exchanges)
- OAuth flows (Coinbase, Kraken, Luno)
- Africa P2P Premium strategy worker (ZAR + NGN)
- FX Rate Service
- WhatsApp Business API notification dispatcher

### Phase 3 — Consumer Dashboard (Weeks 9-12)
- Next.js dashboard (all screens)
- React Native mobile app
- Real-time P&L (Firestore listeners)
- Local currency display (ZAR, NGN, KES, USD)
- Telegram Bot + WhatsApp alerts
- SARS tax export
- PostHog onboarding funnel analytics

### Phase 4 — AI Layer + Scale (Month 4-5)
- Vertex AI Signal Model + Capital Allocation Model
- Naira devaluation event detector
- Weekly automated retraining pipeline
- GKE multi-region deployment
- Cloud CDN low-bandwidth mode (Africa 3G)

### Phase 5 — Institutional + Enterprise (Month 6+)
- CEX-DEX arbitrage
- Institutional REST API (Apigee)
- Performance fee billing
- SOC 2 Type II audit
- French + Portuguese localization

---

*Document v2.0 — June 4, 2026*
