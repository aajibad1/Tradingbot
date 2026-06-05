#!/usr/bin/env python3
"""Stage-2 validation gate — does the edge SURVIVE the execution model?

Stage 1 (`scripts/validate_strategy.py`) asks whether the funding-carry edge
exists at all. It trusts an *analytic* round-trip cost (`ROUND_TRIP_BPS`) and
assumes every position is held for the full 72h and collects all of its realized
funding. That is necessary but optimistic.

Stage 2 takes the SAME real-data entries (`backtest_funding.run_backtest` — the
one source of truth for the entry decision) and replays each one through the
ACTUAL paper-trader execution simulator (`paper-trader/main.simulate_trade`):

  * real per-exchange taker fees on the *filled* notional
  * slippage from the shared size-tier model on both legs
  * partial-fill risk in thin books (residual leg flattened at a cost)
  * **spread-collapse early exit** — the position can be kicked out before 72h,
    so it collects FEWER funding periods than the entry model credited. This is
    the dominant friction stage 1 cannot see.

The per-entry realized funding from the backtest is mapped onto the simulator's
funding model (so funding accrual matches what actually happened over the hold),
and the simulator then decides — per position, with a seeded RNG so the gate is
reproducible — how much of it you really keep after frictions and early exits.

It then builds a daily equity curve from the realized PAPER net PnL and prints a
hard PASS / FAIL against the same family of risk-adjusted thresholds as stage 1,
exiting non-zero on FAIL so it can gate CI / a release.

A stage-1 PASS that turns into a stage-2 FAIL means the execution model eats the
edge — do NOT promote to live. This is stage 2 of the `docs/TRADING_ASSUMPTIONS.md`
promotion ladder; a live paper run on real fills is the final confirmation.

Run:  PYTHONPATH=.:services/paper-trader python3 scripts/paper_run.py \
          [--days 90] [--assets BTC,ETH,SOL] [--seed 1234]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from random import Random

# The simulator logs one INFO line per trade; mute it so the gate output is the
# report, not a per-position trade log.
logging.getLogger("paper-trader").setLevel(logging.WARNING)

from backtest_funding import HOLD_HOURS, SIZE_USD, run_backtest

# Reuse stage-1's equity/risk math so both gates measure identically.
from validate_strategy import (
    GATE_MAX_DD,
    GATE_MIN_SHARPE,
    GATE_MIN_TRADES,
    GATE_MIN_TOTAL,
    annualized_sharpe,
    equity_curve,
    max_drawdown,
)

# paper-trader is on PYTHONPATH (services/paper-trader) — import the SIMULATOR,
# not a reimplementation. simulate_trade is pure + deterministic with rng/now.
from main import simulate_trade  # type: ignore
from models import SimulateRequest  # type: ignore
from shared.models.opportunity import Opportunity, StrategyType

# Carry legs mirror the backtest: long spot on kraken, short the hourly-funding
# hyperliquid perp to collect funding. Book depth on majors >> $10k size, so
# partial fills effectively never trigger here (correct: carry rides liquid
# venues); the live friction stage 2 adds over stage 1 is spread-collapse exit.
LONG_VENUE = "kraken"
SHORT_VENUE = "hyperliquid"
BOOK_DEPTH_USD = 250_000.0
REFERENCE_PRICE = 60_000.0  # carry PnL is funding-on-notional; price is ~neutral here
HOURS_PER_YEAR = 365 * 24


def _annualized_pct_from_realized(realized_funding_bps: float) -> float:
    """Map the backtest's realized funding (signed bps over the full HOLD_HOURS)
    onto the simulator's ``funding_rate_annualized_pct`` input, so that a
    full-hold simulation reproduces exactly that realized funding. When the
    simulator exits early it then collects proportionally fewer periods — which
    is the friction we want stage 2 to expose."""
    hold_fraction = realized_funding_bps / 10_000.0
    return hold_fraction * (HOURS_PER_YEAR / HOLD_HOURS) * 100.0


def _opportunity_for(trade, idx: int) -> Opportunity:
    return Opportunity(
        id=f"paper-run-{trade.asset}-{idx}",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset=trade.asset,
        long_exchange=LONG_VENUE,
        short_exchange=SHORT_VENUE,
        gross_spread_bps=0.0,  # pure carry — no convergence leg
        trading_fees_bps=0.0,  # informational; the simulator recomputes real fees
        slippage_estimate_bps=0.0,
        funding_rate_annualized_pct=_annualized_pct_from_realized(trade.realized_funding_bps),
        net_edge_bps=60.0,  # already cleared the (identical) entry gate in the backtest
        confidence_score=0.8,
        recommended_size_usd=SIZE_USD,
        min_hold_hours=float(HOLD_HOURS),  # planned hold → early-exit window + funding periods
        detected_at=datetime.fromtimestamp(trade.entry_ts_ms / 1000.0, tz=timezone.utc),
        execute=True,
    )


def simulate_paper_pnls(entries, seed: int) -> list[tuple[int, float]]:
    """Replay each backtest entry through the paper-trader simulator.

    Returns ``(entry_ts_ms, net_pnl_usd)`` per simulated position. A per-entry
    RNG derived from ``seed`` keeps the whole run reproducible while still giving
    each position independent early-exit draws."""
    out: list[tuple[int, float]] = []
    for idx, t in enumerate(entries):
        opp = _opportunity_for(t, idx)
        req = SimulateRequest(
            opportunity=opp,
            long_reference_price=REFERENCE_PRICE,
            short_reference_price=REFERENCE_PRICE,
            long_book_depth_usd=BOOK_DEPTH_USD,
            short_book_depth_usd=BOOK_DEPTH_USD,
        )
        now = datetime.fromtimestamp(t.entry_ts_ms / 1000.0, tz=timezone.utc)
        sim = simulate_trade(req, rng=Random(seed + idx), now=now)
        if sim is not None:
            out.append((t.entry_ts_ms, sim.net_pnl_usd))
    return out


def build_daily_returns(pnls: list[tuple[int, float]], n_assets: int, days: int) -> list[float]:
    """Spread each position's realized paper PnL evenly over its 72h hold and
    bucket into daily fractional returns on deployable capital — identical
    methodology to stage 1, but on simulator PnL instead of analytic PnL."""
    if days <= 0 or n_assets <= 0 or not pnls:
        return [0.0] * max(days, 0)
    capital = SIZE_USD * n_assets
    daily_pnl_usd = [0.0] * days
    t0 = min(ts for ts, _ in pnls)
    hold_days = max(1, HOLD_HOURS // 24)
    for ts, pnl in pnls:
        start_day = int((ts - t0) // 86_400_000)
        per_day = pnl / hold_days
        for d in range(start_day, start_day + hold_days):
            if 0 <= d < days:
                daily_pnl_usd[d] += per_day
    return [p / capital for p in daily_pnl_usd]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--assets", type=str, default="BTC,ETH,SOL")
    ap.add_argument("--seed", type=int, default=int(os.environ.get("PAPER_RUN_SEED", 1234)))
    args = ap.parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]

    print("=" * 68)
    print("STAGE-2 PAPER-RUN GATE  (real entries → paper-trader execution model)")
    print("=" * 68)
    print(f"  window      : ~{args.days} days   assets: {', '.join(assets)}   seed: {args.seed}")
    print(f"  thresholds  : Sharpe>{GATE_MIN_SHARPE}  trades>={GATE_MIN_TRADES}  "
          f"maxDD<={GATE_MAX_DD:.0%}  total>{GATE_MIN_TOTAL:.0%}\n")

    res = run_backtest(assets, args.days)
    if not res.trades:
        print("  no entries — funding never cleared the bar in this window.")
        print("\n  RESULT: \033[1;31mFAIL\033[0m (no edge to paper-trade)")
        return 1

    pnls = simulate_paper_pnls(res.trades, args.seed)
    n_trades = len(pnls)
    daily = build_daily_returns(pnls, len(assets), args.days)
    curve = equity_curve(daily)
    sharpe = annualized_sharpe(daily)
    mdd = max_drawdown(curve)
    total_return = curve[-1] - 1.0 if curve else 0.0
    net_usd = [p for _, p in pnls]
    wins = sum(1 for p in net_usd if p > 0)

    print("  PAPER EXECUTION (simulator: fees + slippage + partial fills + spread-collapse exit)")
    print(f"    positions         : {n_trades}")
    print(f"    win rate          : {wins}/{n_trades} = {wins/n_trades:.0%}")
    print(f"    total net PnL      : {sum(net_usd):+,.0f} USD")
    print(f"    mean / worst       : {sum(net_usd)/n_trades:+.1f} / {min(net_usd):+.1f} USD")
    print(f"    total return      : {total_return:+.2%}  (on {SIZE_USD*len(assets):,.0f} deployable)")
    print(f"    annualized Sharpe : {sharpe:.2f}")
    print(f"    max drawdown      : {mdd:.1%}")

    checks = [
        ("Sharpe", f"{sharpe:.2f}", f"> {GATE_MIN_SHARPE}", sharpe > GATE_MIN_SHARPE),
        ("trades", n_trades, f">= {GATE_MIN_TRADES}", n_trades >= GATE_MIN_TRADES),
        ("maxDD", f"{mdd:.1%}", f"<= {GATE_MAX_DD:.0%}", mdd <= GATE_MAX_DD),
        ("total", f"{total_return:+.2%}", f"> {GATE_MIN_TOTAL:.0%}", total_return > GATE_MIN_TOTAL),
    ]
    print("\n  GATE")
    passed = True
    for name, val, want, ok in checks:
        passed &= ok
        mark = "\033[1;32m✓\033[0m" if ok else "\033[1;31m✗\033[0m"
        print(f"    {mark} {name:8} = {val!s:>8}  (need {want})")

    if passed:
        print("\n  RESULT: \033[1;32mPASS\033[0m — edge survived the execution model.")
        print("  NEXT: a live paper run on real fills (trade-ledger BigQuery) is the")
        print("        final confirmation before any real capital.")
        return 0
    print("\n  RESULT: \033[1;31mFAIL\033[0m — execution frictions eroded the edge; do NOT promote.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
