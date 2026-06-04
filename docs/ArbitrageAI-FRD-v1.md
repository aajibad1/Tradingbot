# ArbitrageAI Platform — Functional Requirements Document (FRD)

**Classification:** Internal — Engineering  
**Version:** 1.0  
**Date:** June 4, 2026  
**Prepared for:** Technical Lead & Engineering Team

---

## 1. Authentication & User Management

| ID | Function | Description | Acceptance Criteria |
|---|---|---|---|
| AUTH-01 | Email registration | Register with email + password | Password min 8 chars; email verification within 30s |
| AUTH-02 | Google SSO | One-tap sign in via Firebase Auth | Account created on first login; existing account detected on subsequent |
| AUTH-03 | Apple SSO | Sign in with Apple (iOS required) | Compliant with Apple App Store requirement |
| AUTH-04 | Session management | JWT token refresh | Tokens refresh silently; no force-logout during active session |
| AUTH-05 | Multi-device | Web + mobile simultaneous login | Sessions independent |
| AUTH-06 | Password reset | Email-based reset flow | Reset link expires 1 hour; single-use |
| PROF-01 | Country selection | Home country set on onboarding | Defaults home currency based on country |
| PROF-02 | Currency preference | ZAR, NGN, KES, USD display | Persisted in Firestore; applied to all P&L displays |
| PROF-03 | Notification preferences | Per-channel enable/disable | Effective within 60s |
| PROF-04 | Risk level | Conservative / Balanced / Aggressive | Maps to capital allocation weights |
| PROF-05 | Capital allocation % | % of balance available to AI | Range 10%-100%; default 50% |

---

## 2. Exchange Connectivity

| ID | Function | Description | Acceptance Criteria |
|---|---|---|---|
| EXCH-01 | Coinbase OAuth | OAuth 2.0 authorization code flow | Token in Secret Manager within 5s; balance shown within 3s |
| EXCH-02 | Kraken OAuth | OAuth 2.0 flow | Same as EXCH-01 |
| EXCH-03 | Luno OAuth | OAuth 2.0 flow | Same as EXCH-01; Africa users prioritized |
| EXCH-04 | Token refresh | Automatic OAuth token refresh | Refresh 24h before expiry; user notified on failure |
| EXCH-05 | API key validation | Balance endpoint called on key paste | Connection confirmed within 2s |
| EXCH-06 | Permission guidance | Show which permissions to enable/disable | Withdraw explicitly instructed OFF with screenshot |
| EXCH-07 | Error handling | Human-readable error messages | No raw API error codes shown |
| EXCH-08 | Disconnect detection | Monitor WebSocket health | Reconnect within 10s; user notified after 30s failure |
| EXCH-09 | Balance refresh | Real-time balance display | Updated every 60s; stale indicator if >90s |
| EXCH-10 | Disconnect exchange | Remove exchange connection | API key/token deleted from Secret Manager within 5s |
| EXCH-11 | Reconnect prompt | Alert on expired connection | WhatsApp/Telegram alert + reconnect button |
| EXCH-12 | Key rotation prompt | 90-day rotation reminder | Wizard guides new key generation |

---

## 3. Strategy Engine

| ID | Function | Description | Acceptance Criteria |
|---|---|---|---|
| STRAT-01 | AI engine toggle | Activate/pause all strategies | State changes within 5s; all worker pods start/pause |
| STRAT-02 | Per-exchange toggle | Enable/disable individual exchanges | Takes effect within 30s; no open positions left |
| STRAT-03 | Risk level application | Risk slider adjusts capital allocation | Applied within 60s of change |
| STRAT-04 | Strategy availability by tier | Tier-gated strategy access | Locked strategies show "Upgrade to unlock" |
| OPP-01 | Funding rate polling | Poll Binance, Bybit, OKX funding rates | Every 8 hours; annualized rate calculated |
| OPP-02 | Spatial spread detection | Real-time WebSocket price comparison | Spread detected within 50ms |
| OPP-03 | Africa P2P monitoring | Monitor VALR, Luno, Quidax vs. Binance | Every 5 minutes; ZAR and NGN tracked separately |
| OPP-04 | Naira devaluation detector | Monitor CBN rate vs. P2P rate gap | Alert when gap > 5% |
| EXEC-01 | Profitability pre-filter | Net profit calc before every trade | Trade rejected if < 0.15% after all fees |
| EXEC-02 | Slippage guard | Check slippage before order submission | Reject if actual slippage > tier threshold |
| EXEC-03 | Order placement | Submit via Exchange Connector gRPC | Within 100ms of green-light |
| EXEC-04 | Partial fill handling | Handle < 80% fill within 500ms | Cancel remainder; close hedge immediately |
| EXEC-05 | Hedge closure | Close opposing leg on partial fill | No unhedged legs ever left open |
| EXEC-06 | Execution quality recording | Record expected vs. actual fill | actual_vs_expected_ratio written to Cloud Spanner |
| RISK-01 | Daily loss limit | Auto-pause if daily P&L < threshold | Pause within 5s; WhatsApp/SMS alert sent |
| RISK-02 | Capital per trade limit | Hard limit enforced in order router | Cannot be overridden by AI model |
| RISK-03 | Exchange concentration | Max % on single exchange | Checked before every order |
| RISK-04 | KILL_SWITCH | Platform-wide strategy pause | All Pub/Sub subscriptions paused within 10s |
| RISK-05 | Strategy deprioritization | 7-day underperformance → weight = 0% | Calculated daily by BigQuery job |

---

## 4. FX Rate Service

| ID | Function | Description | Acceptance Criteria |
|---|---|---|---|
| FX-01 | Dashboard FX refresh | Update local currency P&L | Every 60s from Open Exchange Rates API |
| FX-02 | Execution FX capture | Store FX rate at trade close | Written atomically with trade record in Cloud Spanner |
| FX-03 | Nigeria multi-rate display | CBN official, Wise parallel, P2P rate | All three shown; user selects preferred; default P2P |
| FX-04 | FX fallback | Switch to Wise API if primary unavailable | Automatic failover within 5s |

---

## 5. Backtesting

| ID | Function | Description | Acceptance Criteria |
|---|---|---|---|
| BT-01 | Historical data ingestion | OHLCV + funding rates from Tardis.dev | Loaded into BigQuery; partitioned by exchange, asset, date |
| BT-02 | Africa P2P data collection | Scrape P2P premium every 15 minutes | Cloud Function from Day 1; data stored in BigQuery |
| BT-03 | Backtest simulation | Strategy vs. historical data | Results in backtest_results table |
| BT-04 | Performance gate check | Auto-evaluate vs. minimum thresholds | Sharpe > 1.5, drawdown < 10%, win rate > 65%, 6+ months data |
| BT-05 | Gate enforcement | Block live deployment of failing strategies | Strategy marked live_approved: false; deployment halted |
| BT-06 | Paper trading mode | Live data, no real orders | Simulated P&L in separate BigQuery table |

---

## 6. Notifications

| ID | Function | Description | Acceptance Criteria |
|---|---|---|---|
| NOTIF-01 | WhatsApp trade alert | Trade executed alert via WhatsApp | Within 5s of trade close; NGN/ZAR/KES amount shown |
| NOTIF-02 | Telegram trade alert | Trade alert via Telegram Bot | Within 5s; all strategy details included |
| NOTIF-03 | SMS critical alert | SMS for kill switch + loss limit events | Via Twilio within 10s |
| NOTIF-04 | Daily P&L summary | Morning summary at 8:00 AM local time | Skipped if P&L = $0 |
| NOTIF-05 | Quiet market message | Proactive message if no trades for 48h | Explains market conditions |
| NOTIF-06 | Telegram commands | /pnl, /status, /pause, /resume, /balance | Response within 3s |
| NOTIF-07 | Channel routing | Country-based channel selection | NG/KE/GH → WhatsApp; ZA → WhatsApp then Telegram |

---

## 7. Dashboard & Reporting

| ID | Function | Description | Acceptance Criteria |
|---|---|---|---|
| DASH-01 | Real-time portfolio value | Total balance in local currency + USD | Updated every 60s |
| DASH-02 | Today's P&L | Dollar amount + percentage | Updated within 3s of trade close |
| DASH-03 | Active strategy indicators | Live funding rate %, spread %, P2P % | Updated every 60s |
| DASH-04 | P&L history chart | Daily/weekly/monthly Recharts line chart | Timezone-adjusted to user's local time |
| DASH-05 | Trade log | Per-trade detail; paginated (20/page) | Exportable to CSV |
| DASH-06 | SARS tax export | SARS-compliant CSV with ZAR cost base + proceeds | Uses fx_rate_at_execution; South Africa users only |
| DASH-07 | Per-strategy P&L | Breakdown by strategy type | MTD and all-time views |

---

## 8. Subscription & Billing

| ID | Function | Description | Acceptance Criteria |
|---|---|---|---|
| BILL-01 | Free trial | 14-day Pro trial, no payment | Starts on account creation; ends exactly Day 15 |
| BILL-02 | Trial end flow | Engine paused at Day 15 without payment | Dashboard read-only; upgrade prompt with trial P&L |
| BILL-03 | Stripe checkout | Card payment | Subscription active within 60s |
| BILL-04 | Flutterwave checkout | Africa bank transfer + mobile money | Confirmed via webhook; subscription activated |
| BILL-05 | M-Pesa checkout | Kenya mobile money | STK push initiated; activated on confirmation |
| BILL-06 | Tier upgrade | Starter → Pro | Workers upgraded within 60s; new strategies available |
| BILL-07 | Tier downgrade | Pro → Starter | Pro strategies paused at end of billing period |
| BILL-08 | Cancellation retention | P&L-based retention message | Positive P&L → show earnings; zero/negative → offer free month |
| BILL-09 | Subscription webhook | Stripe/Flutterwave events update Firestore | subscription_status updated within 30s |

---

## 9. Admin & Operations

| ID | Function | Description | Acceptance Criteria |
|---|---|---|---|
| ADMIN-01 | Global kill switch | Pause all strategies platform-wide | All workers paused within 10s |
| ADMIN-02 | Per-user kill switch | Pause specific user's strategies | User notified; paused within 5s |
| ADMIN-03 | Strategy approval gate | Manual approval after backtest pass | No automated live deployment without admin approval |
| ADMIN-04 | Exchange blacklist | Blacklist exchange platform-wide | All users' strategies on that exchange paused within 60s |
| ADMIN-05 | User tier override | Manually set user tier | Effective immediately |
| ADMIN-06 | Audit log | All admin actions logged | Immutable; viewable in Cloud Console |

---

*FRD v1.0 — June 4, 2026*
