# Strategy Guide

This guide documents the three live strategies emitted by
`services/opportunity-engine/`. Every strategy must route through
`shared.utils.fee_calculator.calculate_net_edge` (called from `scorer.score`)
so that the viability formula is consistent across the whole system.

Net-edge formula (single source of truth, in
`shared/utils/fee_calculator.py`):

```
net_edge_bps = (
    gross_spread_bps
    + funding_rate_bps_per_period
    - long_exchange_taker_fee_bps
    - short_exchange_taker_fee_bps
    - slippage_long_bps
    - slippage_short_bps
    - transfer_cost_bps
    - borrow_cost_bps
    - safety_buffer_bps   # default 3 bps
)
# Viable when net_edge_bps > MIN_VIABLE_NET_EDGE_BPS (8.0)
```

Per-exchange taker fees (bps), from `EXCHANGE_TAKER_FEE_BPS`:

| Exchange    | Taker fee (bps) |
| ----------- | --------------- |
| coinbase    | 60.0            |
| kraken      | 26.0            |
| crypto.com  | 7.5             |
| binance.us  | 10.0            |
| hyperliquid | 5.0             |

Default slippage tiers (linear, `estimate_size_tier_bps`):
$10K → 2 bps, $50K → 5 bps, $100K → 12 bps.

---

## 1. Cross-Exchange Spot Arbitrage

**Module:** `services/opportunity-engine/strategies/cross_exchange.py`

**Idea.** Two spot venues quote the same asset at different prices. We buy
where the ask is lowest and sell where the bid is highest, capturing the
gross spread minus round-trip taker fees and slippage.

### Entry conditions

- At least two fresh spot ticks for the same asset (one per venue).
- Both venues are in `EXCHANGE_TAKER_FEE_BPS` (otherwise we cannot price the
  fees — fail loud).
- Gross spread `(best_bid - best_ask) / best_ask * 10_000 >= 5 bps` (the
  in-strategy gate; the final `> 8 bps net` check happens after the scorer
  subtracts fees and slippage).
- Risk-engine has not tripped the kill switch.

### Exit conditions

- Spread closes to within 1 bp of mid (typical mean-reversion).
- Time-stop: hold no longer than `min_hold_hours = 0.5` (these spreads
  collapse within minutes on liquid pairs).
- Adverse selection: if either leg gaps against us beyond 2x the original
  spread, close immediately.

### Capital allocation

Default `recommended_size_usd = $10,000`. Scale linearly with venue depth
but never exceed 25% of top-of-book size on either leg to avoid market impact
beyond the `estimate_size_tier_bps` calibration.

### Risk notes

- **Withdrawal / transfer cost** is not added by the scorer here. We assume
  pre-funded balances on both venues; treat any rebalancing transfer as a
  separate cost event, not a per-trade tax.
- This strategy is the most fee-sensitive because both legs are spot. Avoid
  pairs where either venue is `coinbase` (60 bps) unless the spread exceeds
  ~100 bps gross.

### Worked example

Kraken BTC/USD ask = 60,010; crypto.com BTC/USD bid = 60,500.

```
gross_spread_bps = (60_500 - 60_010) / 60_010 * 10_000 ≈ 81.65
long_fee  (kraken)     = 26.0
short_fee (crypto.com) =  7.5
slip_long  ($10K tier) =  2.0
slip_short ($10K tier) =  2.0
buffer                 =  3.0

net_edge_bps = 81.65 - 26.0 - 7.5 - 2.0 - 2.0 - 3.0 = 41.15
```

41.15 > 8 → **viable**. Published to `arb-opportunities`; risk-engine has
final approval.

---

## 2. Spot–Perp Basis

**Module:** `services/opportunity-engine/strategies/spot_perp_basis.py`

**Idea.** A perp can trade rich or cheap relative to spot. We long the
cheaper leg and short the more expensive leg, capturing the basis as it
mean-reverts. Funding either helps (short the rich perp when funding is
positive) or hurts (long a rich perp during positive funding) — the scorer's
`funding_rate_bps_per_period` term carries the sign through.

### Entry conditions

- A fresh spot tick **and** a fresh perp tick for the same asset.
- Both venues are in `EXCHANGE_TAKER_FEE_BPS`.
- `|basis_bps| = |(perp_mid - spot_mid) / spot_mid| * 10_000 >= 5 bps`.
- If perp leg has a known funding rate, that rate is used; otherwise
  `funding_rate_bps_per_period = 0` (the basis still has to clear fees + buffer
  on its own).
- For a Hyperliquid perp leg, funding accrues hourly — the scorer multiplies
  through correctly because we pass `rate_per_period` directly.

### Exit conditions

- Basis narrows to within 1 bp of mid.
- We have collected at least one full funding period of carry and the basis
  is no longer favorable.
- Time-stop: hold no longer than `min_hold_hours = 4.0` unless funding APR
  remains > 10% in our direction.

### Capital allocation

Default $10K per trade. Increase to $25K only on perp legs with > $5M of
top-of-book depth — otherwise slippage on the perp side dominates.

### Risk notes

- Funding flips: a positive perp funding can flip negative mid-hold,
  destroying the carry tailwind. Use the funding-rate-service's persistence
  signal (`>= 4h` consistent direction) before sizing up.
- Basis can widen further before mean-reverting. The 3 bp `safety_buffer_bps`
  in the formula does not protect against multi-standard-deviation moves —
  position-size accordingly.

### Worked example

Kraken BTC/USD spot mid = 60,005; Hyperliquid BTC perp mid = 60,360.
Hyperliquid funding rate = +0.0027 per 8h (~30% APR).

```
basis_bps = (60_360 - 60_005) / 60_005 * 10_000 ≈ 59.16
direction = perp rich → long spot (kraken), short perp (hyperliquid)
funding_bps_per_period = 0.0027 * 10_000 = 27.0  (we collect this)
long_fee  (kraken)        = 26.0
short_fee (hyperliquid)   =  5.0
slip_long  ($10K tier)    =  2.0
slip_short ($10K tier)    =  2.0
buffer                    =  3.0

net_edge_bps = 59.16 + 27.0 - 26.0 - 5.0 - 2.0 - 2.0 - 3.0 = 48.16
```

48.16 > 8 → **viable**.

---

## 3. Funding-Rate Arbitrage (Delta-Neutral Carry)

**Module:** `services/opportunity-engine/strategies/funding_rate_arb.py`

**Idea.** When a perp's funding rate is strongly positive (longs pay shorts),
short the perp to collect funding and long an equivalent amount of spot to
zero out price exposure. For strongly negative funding, flip the legs (the
short-spot side requires margin support and is harder to execute — confidence
is dampened).

### Entry conditions

- A fresh funding rate for the asset (`|annualized_pct| >= 8.0%`).
- A fresh spot tick on some supported venue (we pick the venue with the lowest
  spot ask as the long leg).
- Both legs are in `EXCHANGE_TAKER_FEE_BPS`.
- The basis between spot and perp is not so wide that the perp's spot leg has
  become a directional bet — paired with the spot-perp-basis strategy this is
  rare, since that strategy would already fire.

### Exit conditions

- Funding APR drops below 5% (per-period funding no longer covers a quarter
  of fees + buffer).
- Direction flips (funding turns from positive to negative while held).
- Time-stop: minimum hold is `max(4.0, period_hours)` so that we collect at
  least one full funding payment. There is no hard upper bound — close when
  funding decays.

### Capital allocation

Default $10K per trade. The carry compounds with size on the perp side, but
the spot leg's slippage is the binding constraint. Stay within the $10K-tier
slippage anchor unless we have a real L2 snapshot in `MarketSnapshot`.

### Risk notes

- **Funding can flip.** Use the funding-rate-service's persistence signal
  (`>= 4h` consistent direction) before sizing up — `MIN_VIABLE_NET_EDGE_BPS`
  alone does not protect you from a 1-period flip.
- The scorer treats `funding_rate_bps_per_period` as one funding payment.
  Across multi-period holds you re-publish opportunity records each evaluation
  so the latest funding is always priced in.
- Negative-funding side has confidence multiplied by 0.7 to reflect the
  harder-to-execute short-spot leg.

### Worked example

Kraken BTC/USD spot ask = 60,010; Hyperliquid BTC perp funding =
0.0027 per 8h (~30% APR).

```
gross_spread_bps         = |0.0027 * 10_000| = 27.0   # the funding payment itself
funding_bps_per_period   = 27.0                       # counted again as carry leg
long_ex  = kraken (lowest spot ask)
short_ex = hyperliquid (perp shorted to collect funding)
long_fee  (kraken)        = 26.0
short_fee (hyperliquid)   =  5.0
slip_long  ($10K tier)    =  2.0
slip_short ($10K tier)    =  2.0
buffer                    =  3.0

net_edge_bps = 27.0 + 27.0 - 26.0 - 5.0 - 2.0 - 2.0 - 3.0 = 16.0
```

16.0 > 8 → **viable** for one funding period. If we hold for 3 periods
(24h on a CEX) and funding holds at 27 bps per period, the carry alone
covers fees + buffer ~2.4x over the hold.

---

## Operational notes

- All strategies are pure functions over `MarketSnapshot`. They never call
  out to exchanges directly — only the `market-data` and
  `funding-rate-service` services do.
- The opportunity-engine throttles strategy evaluation to once per
  `EVAL_INTERVAL_S` (default 1.0s). Above-threshold opportunities are
  published freely; the `risk-engine` is the actual choke point and applies
  position limits, exchange concentration limits, the kill switch, and
  drawdown guards.
- To add a new strategy: subclass `strategies.base.Strategy`, return
  `ScoredCandidate` instances, and register it in `strategies/__init__.py`
  plus the `_strategies` list in `main.py`. Do **not** call
  `calculate_net_edge` or read `EXCHANGE_TAKER_FEE_BPS` directly from your
  strategy — that's the scorer's job.
