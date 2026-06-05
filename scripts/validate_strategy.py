#!/usr/bin/env python3
"""Stage-1 validation gate — does the funding-carry edge actually exist?

This is the gate `docs/TRADING_ASSUMPTIONS.md` makes non-negotiable: NO tenant is
promoted to live auto-execution until the strategy clears it. It runs the SAME
real-data backtest engine the live strategy's entry logic uses
(`backtest_funding.run_backtest`), turns the realized trades into a daily equity
curve, and computes the risk-adjusted metrics — then prints a hard PASS / FAIL
against fixed thresholds and exits non-zero on FAIL so it can gate CI / a release.

Thresholds (override via env):
  GATE_MIN_SHARPE   default 1.5   annualized Sharpe of daily returns
  GATE_MIN_TRADES   default 30    need enough trades for the stats to mean anything
  GATE_MAX_DD       default 0.25  max peak-to-trough drawdown of the equity curve
  GATE_MIN_TOTAL    default 0.0   net total return must be positive

Methodology (deliberately CONSERVATIVE — we would rather under-claim edge):
  * Each trade's PnL accrues evenly across its 72h hold (smooths the curve;
    lumping it on entry day would inflate variance and understate Sharpe).
  * Capital base = SIZE_USD per concurrently-tradeable asset, so returns are on
    deployable capital, not just the slice in a position.
  * Flat days (no position) contribute 0% return — they DO count, because idle
    capital is real drag. This makes Sharpe honest, not flattering.

Sharpe on so few, lumpy observations is noisy — a PASS here is necessary, NOT
sufficient. It must still be followed by a live paper run (stage 2) before money.

Run:  PYTHONPATH=. python3 scripts/validate_strategy.py [--days 90] [--assets BTC,ETH,SOL]
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
import sys

from backtest_funding import HOLD_HOURS, SIZE_USD, run_backtest

TRADING_DAYS_PER_YEAR = 365  # crypto trades 24/7


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, default))
    except ValueError:
        return default


GATE_MIN_SHARPE = _f("GATE_MIN_SHARPE", 1.5)
GATE_MIN_TRADES = int(_f("GATE_MIN_TRADES", 30))
GATE_MAX_DD = _f("GATE_MAX_DD", 0.25)
GATE_MIN_TOTAL = _f("GATE_MIN_TOTAL", 0.0)


def build_daily_returns(trades, n_assets: int, days: int) -> list[float]:
    """Spread each trade's PnL evenly over its 72h hold and bucket into daily
    fractional returns on deployable capital (SIZE_USD * n_assets)."""
    if days <= 0 or n_assets <= 0:
        return []
    capital = SIZE_USD * n_assets
    daily_pnl_usd = [0.0] * days
    if not trades:
        return [0.0] * days
    t0 = min(t.entry_ts_ms for t in trades)
    hold_days = max(1, HOLD_HOURS // 24)
    for t in trades:
        pnl_usd = (t.pnl_bps / 10_000.0) * SIZE_USD
        start_day = int((t.entry_ts_ms - t0) // 86_400_000)
        per_day = pnl_usd / hold_days
        for d in range(start_day, start_day + hold_days):
            if 0 <= d < days:
                daily_pnl_usd[d] += per_day
    return [p / capital for p in daily_pnl_usd]


def equity_curve(daily_returns: list[float]) -> list[float]:
    eq, v = [], 1.0
    for r in daily_returns:
        v *= (1.0 + r)
        eq.append(v)
    return eq


def max_drawdown(curve: list[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def annualized_sharpe(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    sd = statistics.pstdev(daily_returns)
    if sd == 0:
        return 0.0
    return (statistics.mean(daily_returns) / sd) * math.sqrt(TRADING_DAYS_PER_YEAR)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--assets", type=str, default="BTC,ETH,SOL")
    args = ap.parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]

    print("=" * 68)
    print("STAGE-1 STRATEGY VALIDATION GATE  (funding-rate carry, real data)")
    print("=" * 68)
    print(f"  window      : ~{args.days} days   assets: {', '.join(assets)}")
    print(f"  thresholds  : Sharpe>{GATE_MIN_SHARPE}  trades>={GATE_MIN_TRADES}  "
          f"maxDD<={GATE_MAX_DD:.0%}  total>{GATE_MIN_TOTAL:.0%}\n")

    res = run_backtest(assets, args.days)
    n_trades = len(res.trades)
    if n_trades == 0:
        print("  no trades — funding never cleared the entry bar in this window.")
        print("\n  RESULT: \033[1;31mFAIL\033[0m (no edge to measure)")
        return 1

    daily = build_daily_returns(res.trades, len(assets), args.days)
    curve = equity_curve(daily)
    sharpe = annualized_sharpe(daily)
    mdd = max_drawdown(curve)
    total_return = curve[-1] - 1.0 if curve else 0.0
    apr = ((1.0 + total_return) ** (365.0 / max(args.days, 1))) - 1.0
    wins = sum(1 for t in res.trades if t.pnl_bps > 0)
    gross_win = sum(t.pnl_bps for t in res.trades if t.pnl_bps > 0)
    gross_loss = -sum(t.pnl_bps for t in res.trades if t.pnl_bps < 0)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else math.inf

    print("  METRICS")
    print(f"    trades            : {n_trades}")
    print(f"    win rate          : {wins}/{n_trades} = {wins/n_trades:.0%}")
    print(f"    total return      : {total_return:+.2%}  (on {SIZE_USD*len(assets):,.0f} deployable)")
    print(f"    annualized (APR)  : {apr:+.1%}")
    print(f"    annualized Sharpe : {sharpe:.2f}")
    print(f"    max drawdown      : {mdd:.1%}")
    print(f"    profit factor     : {profit_factor:.2f}")

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
        print("\n  RESULT: \033[1;32mPASS\033[0m — edge cleared stage 1.")
        print("  NEXT: stage 2 — a live paper run before any real capital. A PASS")
        print("        here is necessary, not sufficient (small, lumpy sample).")
        return 0
    print("\n  RESULT: \033[1;31mFAIL\033[0m — do NOT promote to live auto-execution.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
