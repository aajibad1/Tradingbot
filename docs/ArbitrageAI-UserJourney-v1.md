# ArbitrageAI Platform — User Journey Document

**Classification:** Internal — Product & UX  
**Version:** 1.0  
**Date:** June 4, 2026

---

## Journey 1: Onboarding — African Mobile User (Nigeria)

**Persona:** Emeka, 28, Lagos. Has USDT on Binance P2P. Uses WhatsApp daily. Never heard the term "API key."

### Step 1: Discovery
- Channel: WhatsApp forward from a friend
- Landing: Mobile web landing page
- User sees: "Your crypto works for you while you sleep. No experience needed."
- User action: Taps "Start for free"

### Step 2: Sign Up
- Method: Continue with Google (one tap)
- Backend: Firebase Auth creates account; Firestore profile: country: NG, home_currency: NGN
- Time: < 30 seconds

### Step 3: Country Selection
- Emeka selects: Nigeria
- System: Auto-sets NGN, routes to Nigerian exchange options, prepares WhatsApp notification setup

### Step 4: Connect Exchange — Binance Guided Wizard
- Step 1: "Open Binance" button → deep-link to Binance API Management page
- Step 2: Annotated screenshot: "Tap 'Create API' here" with arrow
- Step 3: Pre-filled label: ArbitrageAI-Emeka (one-tap copy)
- Step 4: Screenshot showing Read Information (checked), Spot Trading (checked), Withdrawals (unchecked)
- Step 5: Two input fields for API Key + Secret Key with paste buttons
- Validation: Balance check in 1.8 seconds
- Confirmation: "Connected! Your Binance balance: 250 USDT (~N375,000)"
- Time: ~3 minutes

### Step 5: WhatsApp Setup
- Screen: "Get alerts on WhatsApp — it's how we'll tell you when you make money"
- Action: Button opens wa.me link with pre-filled message to ArbitrageAI bot
- Bot responds: "Welcome Emeka! Your ArbitrageAI account is connected."
- Time: < 60 seconds

### Step 6: Choose Plan
- Emeka sees NGN pricing: Starter (N13,500/mo), Pro (N43,500/mo)
- "Start your 14-day free trial — no payment needed yet"
- Emeka selects: Pro trial

### Step 7: Activate AI Engine
- Large "AI Engine" toggle shown in OFF state
- Copy: "Your AI will look for opportunities on Binance 24/7. It only trades when it can make a profit."
- After toggle: "Active — Scanning for opportunities"

### Step 8: First Trade (Within Hours)
WhatsApp message:
"Trade Complete!
Strategy: Funding Rate
You earned: N1,840 (~$1.22)
Time: 2 minutes
Total today: N1,840"

### Step 9: Day 14 — Trial Ending
WhatsApp: "Your free trial ends in 2 days. During your trial, your AI made N28,400 (~$18.90).
Keep earning? [Pay with bank transfer] [Pay with card]"
Emeka pays: Flutterwave bank transfer → completed in < 2 minutes

### Step 10: Ongoing Active Use
- Morning: WhatsApp daily summary
- Weekly: Email performance report with NGN P&L chart
- Monthly: Notification if P&L is lower than expected

---

## Journey 2: Onboarding — Global Retail User (USA)

**Persona:** Marcus, 34, Houston TX. Has Coinbase account with $5,000. Not a developer.

### Step 1-2: Discovery + Sign Up
- Channel: Twitter/X crypto influencer post
- Sign up: Continue with Google
- System: Sets country: US, home_currency: USD

### Step 3: Connect Exchange — Coinbase OAuth
- Selects Coinbase → OAuth popup opens
- Coinbase screen: "ArbitrageAI would like to: View your balances and trade on your behalf. (Cannot withdraw funds.)"
- Marcus clicks: Authorize
- Backend: OAuth token stored in Secret Manager
- Confirmation: "Connected! Coinbase balance: $5,042.18"
- Time: < 60 seconds

### Step 4: Telegram Setup
- Marcus has Telegram → "Connect Telegram" button → Telegram deep-link
- Bot: "Connected! I'll alert you every time your AI makes a trade. Type /help for commands."

### Step 5: Plan Selection + Activation
- Marcus selects Pro trial ($29/mo, no card yet)
- Activates AI Engine toggle

### First Telegram Alert (Within Hours)
"Trade Executed
Funding Rate Arb — Coinbase/Bybit
Net profit: +$0.84
All-time: +$0.84"

### Ongoing Telegram Commands
- /pnl → "Today: +$4.12 | This week: +$18.44"
- /status → "Engine: Active | Strategies: Funding Rate (70%), Spatial (30%)"
- /pause → "Engine paused. Type /resume to restart."

---

## Journey 3: Onboarding — South Africa (Thandi)

**Persona:** Thandi, 31, Cape Town. Has VALR account. Wants passive income in ZAR.

### Key Differentiators

**SARS AIT Step (within onboarding):**
"To trade using South African Rand, you'll need a free SARS AIT pin. This takes 5 minutes on the SARS eFiling portal."
- Already have one? Enter it here.
- Don't have one yet? Skip — your AI will use USDT strategies in the meantime.

**Currency:** P&L displayed in ZAR with USD toggle  
**Tax export:** SARS-compliant CSV under Settings → Tax Reports  
**Notifications:** WhatsApp primary  
**Strategy:** Africa P2P Premium (VALR ZAR vs. Binance premium) active for Pro tier

---

## Journey 4: Cancellation & Retention

**Trigger:** User taps "Cancel Subscription"

**Scenario A — Positive P&L this month:**
"Before you go — your AI earned N28,400 this month.
Cancelling means your AI stops working tomorrow.
[Keep my subscription] [Cancel anyway]"

**Scenario B — Zero or negative P&L:**
"We're sorry the results weren't what you expected.
We'd like to give you 1 month free as a thank you.
[Accept free month] [Cancel anyway]"

**Scenario C — User cancels anyway:**
- Dashboard set to read-only; trade history retained 12 months
- WhatsApp/Telegram message 30 days later: "Miss earning? Reactivate in one tap."

---

## Journey 5: Quiet Market Period

**Trigger:** No profitable opportunities for 48 hours

**Automated WhatsApp/Telegram message:**
"Market Update
Arbitrage opportunities were quieter than usual this week.
Your AI ran 3 trades with +N840 net profit.
We're watching for better conditions — your capital is safe and your engine stays active.
We'll message you the moment a good opportunity appears."

**Purpose:** Prevents cancellation from silence. Users understand low periods are normal.

---

## Key Activation & Retention Metrics (PostHog)

| Metric | Target |
|---|---|
| Onboarding completion rate | > 85% |
| Time to first exchange connection | < 4 minutes |
| Time to first trade | < 24 hours |
| Day 14 trial conversion | > 35% |
| Monthly churn rate | < 5% |
| WhatsApp notification open rate (Africa) | > 70% |
| Telegram command usage (Pro) | > 40% monthly |
| P&L-positive users | > 80% in any given month |

---

*User Journey Document v1.0 — June 4, 2026*
