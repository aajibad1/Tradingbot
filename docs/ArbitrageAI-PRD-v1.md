# ArbitrageAI Platform — Product Requirements Document (PRD)

**Classification:** Internal — Product & Engineering  
**Version:** 1.0  
**Date:** June 4, 2026  
**Prepared for:** Technical Lead, Product Manager, Engineering Team

---

## 1. Product Purpose

ArbitrageAI is a subscription SaaS platform that enables non-technical cryptocurrency users to automatically earn profits through AI-driven arbitrage trading. Users connect their existing exchange accounts, activate the AI engine, and the system handles all strategy execution, risk management, and reporting automatically.

**Primary Goal:** Help users make money from crypto arbitrage without any technical knowledge.  
**Secondary Goal:** Build a globally scalable, Africa-first platform with persistent recurring revenue.

---

## 2. Target Users

### Persona 1: The African Mobile User (Nigeria / South Africa / Kenya)
- Age: 22-40 | Device: Android smartphone
- Crypto knowledge: Owns USDT or BTC for remittances or inflation hedging
- Technical level: Uses WhatsApp, mobile banking (M-Pesa, OPay, Capitec)
- Primary notification: WhatsApp
- Primary payment: Mobile money (M-Pesa, Flutterwave), bank transfer

### Persona 2: The Global Retail Crypto Holder
- Age: 25-45 | Device: Desktop + iPhone
- Crypto knowledge: Holds BTC/ETH on Coinbase or Binance
- Technical level: Uses apps comfortably, not a developer
- Primary notification: Telegram or Push
- Primary payment: Credit card (Stripe)

### Persona 3: The Active Crypto Trader (Pro/Institutional)
- Age: 28-50 | Understands funding rates, perpetuals, CEX/DEX
- Pain point: Manual arbitrage is exhausting and speed-limited
- Primary notification: Telegram two-way commands

---

## 3. Key Requirements

### 3.1 Exchange Connectivity

| Req ID | Requirement | Priority |
|---|---|---|
| EC-01 | Users connect exchanges without knowing what an API key is | P0 |
| EC-02 | OAuth 2.0 flow for Coinbase, Kraken, Luno | P0 |
| EC-03 | Guided API key wizard for Binance, Bybit, VALR | P0 |
| EC-04 | Wizard validates connection and shows balance within 2 seconds | P0 |
| EC-05 | Wizard explicitly instructs disabling Withdraw permissions | P0 |
| EC-06 | Human-readable error messages — no technical jargon | P0 |
| EC-07 | Support 2 exchanges (Starter), 10 (Pro), 20 (Institutional) | P0 |
| EC-08 | SARS AIT pin wizard for South African users | P1 |
| EC-09 | BVN/NIN KYC verification for Nigerian users | P1 |

### 3.2 Strategy Execution

| Req ID | Requirement | Priority |
|---|---|---|
| SE-01 | Funding rate arbitrage automated (Starter+) | P0 |
| SE-02 | Spatial arbitrage (Pro+) | P0 |
| SE-03 | Africa P2P premium trading (Africa Pro+) | P0 |
| SE-04 | No trade executes unless net profit > 0.15% after all fees | P0 |
| SE-05 | Every strategy passes backtesting gates before live deployment | P0 |
| SE-06 | Partial fills handled: unhedged legs never left open | P0 |
| SE-07 | Statistical/pairs arbitrage (Pro+ Phase 4) | P1 |
| SE-08 | CEX-DEX arbitrage (Institutional Phase 5) | P2 |

### 3.3 Risk Management

| Req ID | Requirement | Priority |
|---|---|---|
| RM-01 | Per-user daily loss limit auto-pause | P0 |
| RM-02 | Max capital per trade limits enforced per tier | P0 |
| RM-03 | Slippage guard: reject order if actual slippage > threshold | P0 |
| RM-04 | Platform-wide KILL_SWITCH pauses all strategies globally | P0 |
| RM-05 | Post-trade execution quality reconciliation | P0 |
| RM-06 | Strategy auto-deprioritization after 7-day underperformance | P1 |

### 3.4 Dashboard & Reporting

| Req ID | Requirement | Priority |
|---|---|---|
| DR-01 | Real-time P&L in user's local currency | P0 |
| DR-02 | FX rate refreshed every 60 seconds | P0 |
| DR-03 | FX rate stored at execution timestamp for tax reporting | P0 |
| DR-04 | SARS-compliant tax export CSV (South Africa) | P1 |
| DR-05 | FIRS format export (Nigeria, Phase 3) | P1 |
| DR-06 | Per-strategy P&L breakdown | P1 |
| DR-07 | Trade-by-trade log with full detail | P1 |
| DR-08 | Nigeria: three-rate FX display (CBN, Wise, P2P) | P1 |

### 3.5 Notifications

| Req ID | Requirement | Priority |
|---|---|---|
| NT-01 | WhatsApp Business API alerts for Africa users | P0 |
| NT-02 | Telegram Bot alerts for global users | P0 |
| NT-03 | SMS fallback for all critical alerts | P0 |
| NT-04 | Push notifications (iOS + Android) | P0 |
| NT-05 | Email daily digest + weekly performance report | P1 |
| NT-06 | Telegram two-way commands | P1 |
| NT-07 | Slack webhook for Institutional/Enterprise | P2 |
| NT-08 | Quiet market proactive communication | P1 |

### 3.6 Subscription & Billing

| Req ID | Requirement | Priority |
|---|---|---|
| SB-01 | 14-day free trial (no credit card required) | P0 |
| SB-02 | Stripe billing for card payments (global) | P0 |
| SB-03 | Flutterwave for Africa bank transfer + mobile money | P0 |
| SB-04 | Paystack for South Africa EFT | P0 |
| SB-05 | M-Pesa via Flutterwave (Kenya) | P0 |
| SB-06 | Automated tier upgrade/downgrade within 60 seconds | P0 |
| SB-07 | Churn mitigation: P&L-based cancel retention message | P1 |
| SB-08 | Performance fee add-on (Pro+, Phase 5) | P2 |

### 3.7 Compliance & Security

| Req ID | Requirement | Priority |
|---|---|---|
| CS-01 | Platform never holds, moves, or controls user funds | P0 |
| CS-02 | API keys scoped to Read + Trade only (no Withdraw) | P0 |
| CS-03 | All credentials in GCP Secret Manager — never in DB | P0 |
| CS-04 | Per-user secret isolation | P0 |
| CS-05 | GDPR compliance (EU users) | P0 |
| CS-06 | POPIA compliance (South Africa) | P1 |
| CS-07 | NDPA compliance (Nigeria) | P1 |
| CS-08 | FSCA CASP registration | P1 |
| CS-09 | SEC Nigeria Digital Asset registration | P1 |
| CS-10 | Kenya VASP Act registration | P1 |

---

## 4. Out of Scope (v1.0)

- Platform custody of user funds
- Nigeria P2P fiat (NGN bank transfer) leg automation (legal review required)
- CEX-DEX arbitrage (Phase 5)
- Copy trading or social trading features
- Crypto-to-fiat off-ramp
- Leveraged positions beyond exchange-native perpetuals

---

*PRD v1.0 — June 4, 2026*
