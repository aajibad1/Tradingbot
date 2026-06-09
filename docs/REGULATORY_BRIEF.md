# Regulatory briefing — for counsel

**Purpose:** a plain-language description of what we are building so a fintech /
securities lawyer can scope licensing across our launch markets. This is an
engineering description, **not legal advice or a legal conclusion.**

## What the platform does

A SaaS platform that runs **crypto arbitrage on behalf of end users**. Under the
chosen model (see `docs/PLATFORM_ARCHITECTURE.md`):

- **Users do not bring their own exchange API keys.** They sign up, complete
  KYC/KYB, subscribe (Stripe), and **fund an account**.
- **The platform holds the funds and trades them** on the platform's own exchange
  accounts (centralized exchanges; later stablecoin rails / on-chain venues).
- Strategies are **delta-neutral arbitrage** (funding-rate carry, spot-perp basis,
  cross-exchange) — designed to be market-neutral, not directional bets.
- Profit/loss accrues to the user's internal balance; users can **withdraw**.
- **Two markets:** Global (crypto exchange execution) and **Africa** (cross-border
  FX / stablecoin **corridor** settlement via local/mobile-money rails).

## The facts that drive licensing (please assess)

1. **Custody of customer funds.** We receive, hold, and disburse customer money
   and crypto. → money-transmitter / e-money / VASP custody questions.
2. **Discretionary trading on customers' behalf.** The platform decides and
   executes trades. → investment-adviser / asset-management / discretionary-mandate
   questions.
3. **Pooled vs segregated.** Open design question with legal consequences:
   - *Pooled* (one platform book; users hold a claim on PnL) may look like a
     **collective investment scheme / fund**.
   - *Segregated* (per-user sub-balances, per-user execution) is closer to
     custody + execution services. **We need your steer on which is viable.**
4. **Yield/return representations.** Marketing "returns" from arbitrage may trigger
   **securities** characterization. We want guidance on permissible language.
5. **Cross-border settlement (Africa).** Moving value across corridors via
   stablecoins, banks, and **mobile money** → money-transmission / remittance /
   FX-dealing licensing, **per country**.
6. **Subscription model.** Users pay a SaaS subscription (Stripe); the trading
   benefit is separate. Does fee structure affect characterization?

## Launch jurisdictions to scope

| Market | Likely regimes to assess |
|---|---|
| United States | FinCEN MSB + **state-by-state MTLs**; SEC/CFTC if deemed securities/derivatives; investment-adviser status |
| Nigeria | SEC digital-asset rules; CBN payment/FX; VASP registration |
| South Africa | FSCA **CASP** licensing; FIC (AML); Reserve Bank exchange-control/FX |
| Kenya | Evolving VASP framework; CBK payments/FX |
| Ghana | Bank of Ghana digital-asset/payments posture |

## Specific questions for counsel

1. Under each market, does **holding + trading customer funds** require licensing,
   and which license(s)?
2. Does **pooled** custody/trading create fund/CIS exposure we should avoid by
   going **segregated**?
3. Is **discretionary arbitrage trading** an investment-advisory / asset-management
   activity requiring registration?
4. What **disclosures, risk warnings, and customer agreements** are mandatory
   before we hold a single dollar?
5. Africa corridors: which **money-transmission / remittance / FX** licenses apply
   per country, and can stablecoin rails reduce or change the licensing surface?
6. Is there a **launch sequence** (e.g. one jurisdiction, segregated model,
   non-discretionary first) that materially lowers the initial licensing burden?

## Derivatives / futures exposure (please assess)

The strategies use **perpetual futures** (perps) as a market-neutral leg: funding
carry = long spot + **short perp** at 1× isolated margin, fully hedged — we are
harvesting funding, not making a levered directional bet. Separately, the system
supports an optional, **budget-capped directional sleeve** (small net directional
exposure for the hybrid model) and a dormant quarterly-futures basis strategy.

Questions for counsel:
1. Does **market-neutral perp usage** (1×, hedged, on the platform's own accounts)
   carry different derivatives licensing than **levered directional futures**?
2. Crypto **derivatives for end users** are restricted/banned for retail in several
   target jurisdictions (US retail; parts of Africa). Under managed custody, what
   can we offer, to whom, and what licensing does directional/levered futures
   exposure trigger beyond spot?
3. Should the **directional sleeve be off by default per region** (it already is
   globally — `max_directional_exposure_pct = 0.0`) pending per-jurisdiction
   clearance?

## AI / automated decision-making (governance)

AI (Perplexity for cited market research, Claude for reasoning, multi-agent
debate, an advisory ranker) is used **only in an advisory plane** — it ranks,
researches, and explains. The **deterministic risk engine is the sole final
authority**; AI never sends trades or bypasses hard controls, and every AI output
is persisted with its **sources/citations + model version** for audit. Questions:
do any jurisdictions require disclosure of automated/algorithmic decision-making
to customers, and are there suitability/robo-advisory rules we must meet?

## What we will NOT do before clearance

- Hold or move **real customer money** (everything runs in **paper mode** with an
  internal ledger until cleared — `live_enabled=false` is enforced in code).
- Make return/yield representations in marketing.
- Operate corridor payouts without the relevant money-transmission licensing.

## Engineering status (context)

Built and paper-mode-tested today: market data, opportunity detection, risk
engine (kill switch + limits), paper execution, an internal identity/tenant/RBAC
+ onboarding (KYC/region/policy) service. **No real customer funds are held or
moved.** The internal ledger and funding/payout *interfaces* are being built in
paper mode so the engineering is ready when (and only when) licensing clears.
