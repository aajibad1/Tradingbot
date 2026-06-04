# Trading assumptions & how to re-calibrate them

The entry logic has a few **tunable assumptions** that materially change which
opportunities are considered profitable. They were calibrated against the
*simulator* (`scripts/stress_test.py`), not real markets — so they are starting
points to be re-fit from real fills, not settled truths. This note records what
they are, why they exist, and how to re-calibrate.

## 1. Net-edge viability bar — `MIN_VIABLE_NET_EDGE_BPS = 50.0`
`shared/utils/fee_calculator.py`. An opportunity is viable only when
`net_edge_bps > 50`. It is deliberately conservative: round-trip taker fees +
slippage + a safety buffer run ~38 bps at $10k, so the bar leaves real margin
after costs.

**Why it matters (stress test):** taking *every* opportunity loses ~−37 bps/trade
(you pay ~38 bps to capture spreads that are usually 1–15 bps). The bar admits
only the ~3–4% genuine dislocations, which are reliably net-positive in-model.
**The bar is the source of edge — do not lower it without a fee/slippage
improvement to match.**

## 2. Funding-carry is a MULTI-PERIOD edge
A delta-neutral funding trade collects funding **every period** over its hold
while paying round-trip costs **once**. `calculate_net_edge` credits
`funding_rate_bps_per_period × funding_periods` (not a single period). A
single-period model rejected *all* realistic funding (0 viable in the stress
test); the multi-period model makes carry tradeable.

## 3. Carry hold horizon — `FUNDING_TARGET_HOLD_HOURS` (default 72h, **hard-capped at 72h**)
`services/opportunity-engine/strategies/funding_rate_arb.py`. The assumed hold
sets `funding_periods = hold // period_hours`. **Longer assumed holds manufacture
fake edge:** the stress sweep showed modeled edge running 1.5–10× realized as the
hold grew, with the *median* trade going negative by ~14 days. The hold is
therefore hard-capped (`_MAX_CARRY_HOLD_HOURS = 72`); the env var can only lower
it. Do **not** raise the cap to book more trades.

## 4. Funding persistence haircut — `FUNDING_PERSISTENCE` (default 0.6)
`calculate_net_edge(..., funding_persistence=...)`, passed by the strategy.
Credited funding is multiplied by this factor to account for **early exit** (the
position closes before the full hold) and **funding mean-reversion** (later
periods earn less than the entry rate). Calibrated so the modeled edge tracks
simulated realized PnL instead of running optimistic.

**Effect (stress test @ 72h cap, 0.6 haircut):** funding-arb 2.7% viable, +76 bps
realized, 83% win, modeled +74 vs realized +76 (gap ≈ 0). Without the haircut the
gap was ~+60 bps (model thought trades were ~2× better than realized).

## How to re-calibrate from REAL fills

Once live/paper fills exist (trade-ledger BigQuery `arb_trading.trades`):

1. **Persistence (`FUNDING_PERSISTENCE`)** — for closed funding trades, compute
   `realized_funding / entry_credited_funding` (entry-credited =
   `funding_per_period × planned_periods`). Set the factor to the *median* of that
   ratio. If real funding persists better/worse than the simulator assumed, this
   moves off 0.6.
2. **Hold cap (`FUNDING_TARGET_HOLD_HOURS` / `_MAX_CARRY_HOLD_HOURS`)** — measure
   the actual hold before exit (early-exit/funding-flip) across closed carry
   trades. Set the planned hold at/below the *median* realized hold so the entry
   model doesn't credit periods you don't actually capture.
3. **Re-validate** with `PYTHONPATH=.:services/paper-trader python3
   scripts/stress_test.py` — but replace its synthetic scenario generators with
   the empirical funding/spread distributions from BigQuery. The goal is
   `modeled ≈ realized` (gap near zero) at the chosen settings.

## Hard limits that are NOT tunable
- `risk-engine` kill switch, drawdown / position / leverage limits (`DEFAULT_LIMITS`).
- ai-ops `NEVER`-tier (withdrawals, leverage increases) and the PROPOSE allowlist.
- These are risk controls, not strategy knobs — change them only through review.
