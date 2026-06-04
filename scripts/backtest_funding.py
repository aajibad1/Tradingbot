#!/usr/bin/env python3
"""Historical backtest of the funding-rate carry strategy on REAL funding data.

Pulls real hyperliquid funding history (free, public), then walks non-overlapping
carry windows: at each window start it makes the REAL entry decision
(calculate_net_edge with the multi-period model + 0.6 persistence haircut + 72h
hold), and for the windows it would enter, computes realized PnL from the ACTUAL
subsequent funding over the hold (signed — funding can flip against you) minus
round-trip costs.

This is the first real-data profitability signal for carry, and it measures the
EMPIRICAL funding persistence so the synthetic 0.6 haircut can be re-calibrated.

Run:  PYTHONPATH=. python3 scripts/backtest_funding.py [--days 45]
"""
from __future__ import annotations

import argparse
import statistics
import time

import ccxt

from shared.utils.fee_calculator import calculate_net_edge, taker_fee_bps
from shared.utils.slippage_model import estimate_size_tier_bps

DEFAULT_ASSETS = ["BTC", "ETH", "SOL"]
HOLD_HOURS = 72
PERIOD_HOURS = 1                  # hyperliquid funds hourly
PERSISTENCE = 0.6                 # the synthetic haircut we want to validate
SIZE_USD = 10_000.0
LONG_SPOT_VENUE = "kraken"        # spot leg (long); short the HL perp to collect funding
SLIP = estimate_size_tier_bps(SIZE_USD)
LONG_FEE = taker_fee_bps(LONG_SPOT_VENUE)
SHORT_FEE = taker_fee_bps("hyperliquid")
# Round-trip cost in bps (fees + slippage both legs + safety buffer).
ROUND_TRIP_BPS = LONG_FEE + SHORT_FEE + 2 * SLIP + 3.0


def fetch_funding_series(asset: str, days: int) -> list[float]:
    """Return an ordered list of hourly funding rates (decimal/period) for the window."""
    ex = ccxt.hyperliquid({"timeout": 10000, "enableRateLimit": True})
    sym = f"{asset}/USDC:USDC"
    since = ex.milliseconds() - days * 86_400_000
    by_ts: dict[int, float] = {}
    for _ in range(40):  # page cap
        chunk = ex.fetch_funding_rate_history(sym, since=since, limit=500)
        if not chunk:
            break
        for r in chunk:
            if r.get("fundingRate") is not None:
                by_ts[int(r["timestamp"])] = float(r["fundingRate"])
        nxt = int(chunk[-1]["timestamp"]) + 1
        if nxt <= since:
            break
        since = nxt
        if since >= ex.milliseconds() - 3_600_000:
            break
        time.sleep(ex.rateLimit / 1000)
    return [rate for _, rate in sorted(by_ts.items())]


def gate_viable(entry_rate: float) -> tuple[float, bool]:
    """Entry decision: the real multi-period + haircut net-edge gate."""
    r = calculate_net_edge(
        gross_spread_bps=0.0,
        long_exchange_taker_fee_bps=LONG_FEE, short_exchange_taker_fee_bps=SHORT_FEE,
        slippage_long_bps=SLIP, slippage_short_bps=SLIP,
        funding_rate_bps_per_period=entry_rate * 10_000.0,
        funding_periods=HOLD_HOURS // PERIOD_HOURS, funding_persistence=PERSISTENCE,
    )
    return r["net_edge_bps"], r["is_viable"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--assets", type=str, default=",".join(DEFAULT_ASSETS),
                    help="comma-separated HL perp bases, e.g. ZRO,HYPE,DYDX")
    args = ap.parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]

    print(f"Round-trip cost assumed: {ROUND_TRIP_BPS:.1f} bps "
          f"(long {LONG_SPOT_VENUE} {LONG_FEE:.0f} + short HL {SHORT_FEE:.0f} + slip {2*SLIP:.0f} + buffer 3)\n")

    all_pnl, all_persist, total_windows, total_viable, pos_windows = [], [], 0, 0, 0
    for asset in assets:
        rates = fetch_funding_series(asset, args.days)
        if len(rates) < HOLD_HOURS + 1:
            print(f"{asset:4} insufficient history ({len(rates)} hrs)")
            continue
        # annualized APR series for context
        aprs = [r * 8760 * 100 for r in rates]
        # Continuous hourly entry, one position at a time: when flat, enter the
        # moment funding clears the bar, hold HOLD_HOURS, then go flat. This is
        # how the live strategy behaves and is the only way to catch transient
        # funding spikes (a 72h grid misses spikes that don't land on a boundary).
        a_pnl = []
        i, n = 0, len(rates) - HOLD_HOURS
        while i < n:
            total_windows += 1
            entry_rate = rates[i]
            if entry_rate <= 0:           # positive-funding carry only (short perp collects)
                i += 1
                continue
            pos_windows += 1
            _, viable = gate_viable(entry_rate)
            if not viable:
                i += 1
                continue
            total_viable += 1
            realized_funding_bps = sum(rates[i:i + HOLD_HOURS]) * 10_000.0   # signed actual
            pnl = realized_funding_bps - ROUND_TRIP_BPS
            credited = entry_rate * 10_000.0 * HOLD_HOURS                    # un-haircut entry credit
            a_pnl.append(pnl)
            all_pnl.append(pnl)
            if credited > 0:
                all_persist.append(realized_funding_bps / credited)
            i += HOLD_HOURS               # locked in the position for the hold
        hrs = len(rates)
        print(f"{asset:4} {hrs:>5} hrs  APR range [{min(aprs):+.0f}%, {max(aprs):+.0f}%]  "
              f"median {statistics.median(aprs):+.1f}%  | entered {len(a_pnl)}"
              + (f", realized mean {statistics.mean(a_pnl):+.1f} bps" if a_pnl else ""))

    print("\n" + "=" * 64)
    print(f"Backtest window: ~{args.days} days, {HOLD_HOURS}h non-overlapping carry windows")
    print(f"  hourly entry checks (while flat): {total_windows}")
    print(f"  positive-funding checks         : {pos_windows}")
    print(f"  entries (cleared the bar)       : {total_viable}"
          + (f"  ({total_viable/pos_windows:.2%} of positive)" if pos_windows else ""))
    if all_pnl:
        wins = sum(1 for p in all_pnl if p > 0)
        print(f"\n  TRADED carry windows ({len(all_pnl)}):")
        print(f"    win rate     : {wins}/{len(all_pnl)} = {wins/len(all_pnl):.0%}")
        print(f"    realized PnL : mean {statistics.mean(all_pnl):+.1f} bps, "
              f"median {statistics.median(all_pnl):+.1f} bps, "
              f"best {max(all_pnl):+.1f}, worst {min(all_pnl):+.1f}")
        print(f"    total        : {sum(all_pnl):+.1f} bps across all carry windows")
    if all_persist:
        emp = statistics.median(all_persist)
        print(f"\n  EMPIRICAL funding persistence (realized/credited): median {emp:.2f}")
        print(f"    → synthetic haircut assumed {PERSISTENCE:.2f}. "
              + ("Real funding held up BETTER than assumed — 0.6 is conservative."
                 if emp > PERSISTENCE else
                 "Real funding decayed MORE than assumed — 0.6 is too optimistic; lower it."))
    else:
        print("\n  No viable carry windows in this period — funding stayed below the bar")
        print("  (typical: carry only clears the bar during funding spikes/squeezes).")


if __name__ == "__main__":
    main()
