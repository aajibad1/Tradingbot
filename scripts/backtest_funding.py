#!/usr/bin/env python3
"""Historical backtest of the funding-rate carry strategy on REAL funding data.

Pulls real hyperliquid funding history (free, public), then walks carry windows:
at each window start it makes the REAL entry decision (calculate_net_edge with
the multi-period model + 0.6 persistence haircut + 72h hold), and for the windows
it would enter, computes realized PnL from the ACTUAL subsequent funding over the
hold (signed — funding can flip against you) minus round-trip costs.

This is the first real-data profitability signal for carry, and it measures the
EMPIRICAL funding persistence so the synthetic 0.6 haircut can be re-calibrated.

The engine is exposed as ``run_backtest()`` (returns timestamped ``Trade`` records)
so the stage-1 validation gate (``scripts/validate_strategy.py``) can compute
Sharpe / drawdown / PASS-FAIL on top of the same entry logic — one source of
truth for the strategy decision.

Run:  PYTHONPATH=. python3 scripts/backtest_funding.py [--days 45] [--assets BTC,ETH]
"""
from __future__ import annotations

import argparse
import statistics
import time
from dataclasses import dataclass, field

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


@dataclass
class Trade:
    """One realized carry position."""
    asset: str
    entry_ts_ms: int
    pnl_bps: float               # realized funding minus round-trip cost (signed)
    realized_funding_bps: float  # actual signed funding collected over the hold
    credited_bps: float          # un-haircut entry credit (for persistence calc)


@dataclass
class AssetSummary:
    hrs: int
    apr_min: float
    apr_max: float
    apr_median: float
    entered: int
    mean_pnl_bps: float | None


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    per_asset: dict[str, AssetSummary] = field(default_factory=dict)
    total_windows: int = 0
    pos_windows: int = 0
    total_viable: int = 0
    days: int = 0
    size_usd: float = SIZE_USD
    round_trip_bps: float = ROUND_TRIP_BPS


def fetch_funding_series(asset: str, days: int) -> list[tuple[int, float]]:
    """Return ordered ``(timestamp_ms, funding_rate)`` pairs for the window
    (hourly funding, decimal/period)."""
    import ccxt

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
    return sorted(by_ts.items())


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


def run_backtest(assets: list[str], days: int,
                 fetch=fetch_funding_series) -> BacktestResult:
    """Walk the real funding series and return every realized carry trade.

    ``fetch`` is injectable so tests can feed synthetic series without network.
    The entry policy (continuous hourly entry, one position at a time per asset,
    positive-funding carry only) mirrors the live strategy.
    """
    res = BacktestResult(days=days)
    for asset in assets:
        series = fetch(asset, days)
        rates = [r for _, r in series]
        ts = [t for t, _ in series]
        if len(rates) < HOLD_HOURS + 1:
            continue
        aprs = [r * 8760 * 100 for r in rates]
        a_pnl: list[float] = []
        i, n = 0, len(rates) - HOLD_HOURS
        while i < n:
            res.total_windows += 1
            entry_rate = rates[i]
            if entry_rate <= 0:           # positive-funding carry only (short perp collects)
                i += 1
                continue
            res.pos_windows += 1
            _, viable = gate_viable(entry_rate)
            if not viable:
                i += 1
                continue
            res.total_viable += 1
            realized_funding_bps = sum(rates[i:i + HOLD_HOURS]) * 10_000.0   # signed actual
            pnl = realized_funding_bps - ROUND_TRIP_BPS
            credited = entry_rate * 10_000.0 * HOLD_HOURS                    # un-haircut entry credit
            res.trades.append(Trade(asset, ts[i], pnl, realized_funding_bps, credited))
            a_pnl.append(pnl)
            i += HOLD_HOURS               # locked in the position for the hold
        res.per_asset[asset] = AssetSummary(
            hrs=len(rates), apr_min=min(aprs), apr_max=max(aprs),
            apr_median=statistics.median(aprs), entered=len(a_pnl),
            mean_pnl_bps=statistics.mean(a_pnl) if a_pnl else None,
        )
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--assets", type=str, default=",".join(DEFAULT_ASSETS),
                    help="comma-separated HL perp bases, e.g. ZRO,HYPE,DYDX")
    args = ap.parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]

    print(f"Round-trip cost assumed: {ROUND_TRIP_BPS:.1f} bps "
          f"(long {LONG_SPOT_VENUE} {LONG_FEE:.0f} + short HL {SHORT_FEE:.0f} + slip {2*SLIP:.0f} + buffer 3)\n")

    res = run_backtest(assets, args.days)
    for asset in assets:
        s = res.per_asset.get(asset)
        if s is None:
            print(f"{asset:4} insufficient history")
            continue
        print(f"{asset:4} {s.hrs:>5} hrs  APR range [{s.apr_min:+.0f}%, {s.apr_max:+.0f}%]  "
              f"median {s.apr_median:+.1f}%  | entered {s.entered}"
              + (f", realized mean {s.mean_pnl_bps:+.1f} bps" if s.mean_pnl_bps is not None else ""))

    all_pnl = [t.pnl_bps for t in res.trades]
    all_persist = [t.realized_funding_bps / t.credited_bps
                   for t in res.trades if t.credited_bps > 0]

    print("\n" + "=" * 64)
    print(f"Backtest window: ~{args.days} days, {HOLD_HOURS}h non-overlapping carry windows")
    print(f"  hourly entry checks (while flat): {res.total_windows}")
    print(f"  positive-funding checks         : {res.pos_windows}")
    print(f"  entries (cleared the bar)       : {res.total_viable}"
          + (f"  ({res.total_viable/res.pos_windows:.2%} of positive)" if res.pos_windows else ""))
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
