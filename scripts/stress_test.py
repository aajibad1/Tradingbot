#!/usr/bin/env python3
"""Monte-Carlo paper-trading stress test.

Drives thousands of synthetic market scenarios through the REAL logic:
  * entry decision  → shared.utils.fee_calculator.calculate_net_edge (the SSoT)
  * realized PnL    → paper-trader's production _simulate_internal
                      (fees, slippage, partial fills, funding accrual, spread collapse)

It answers: "given the system's OWN models, is the entry logic internally
consistent, does it filter losers, and is the modeled expected value positive —
and what patterns fall out?"

IMPORTANT — what this is NOT: it is not a market backtest and proves nothing
about real profitability. Scenario distributions and the simulator's slippage /
spread-collapse / funding assumptions are themselves modelling choices. A
positive result here is necessary, not sufficient.

Run:  PYTHONPATH=.:services/paper-trader python3 scripts/stress_test.py [--n 30000] [--seed 7]
"""

from __future__ import annotations

import argparse
import logging
import random
import statistics
from datetime import datetime

# Quiet the NullPublisher / trade logging the simulator emits per call.
logging.disable(logging.CRITICAL)

from shared.models.opportunity import Opportunity, StrategyType  # noqa: E402
from shared.utils.fee_calculator import calculate_net_edge, taker_fee_bps  # noqa: E402
from shared.utils.slippage_model import estimate_size_tier_bps  # noqa: E402

import main as paper_trader  # paper-trader/main.py  # noqa: E402
from models import SimulateRequest  # paper-trader/models.py  # noqa: E402

EXCHANGES = ["coinbase", "kraken", "crypto.com", "binance.us", "hyperliquid"]
PERP_VENUES = {"hyperliquid"}  # 1h funding; others 8h
ASSET_PRICES = {"BTC": 60_000.0, "ETH": 3_000.0, "SOL": 150.0}
HOURS_PER_YEAR = 365 * 24


def _period_hours(exchange: str) -> float:
    return 1.0 if exchange in PERP_VENUES else 8.0


def gen_cross_exchange(rng: random.Random) -> dict:
    """Cross-exchange spread arb. Spreads are mostly tiny with a dislocation tail."""
    long_ex, short_ex = rng.sample(EXCHANGES, 2)
    asset = rng.choice(list(ASSET_PRICES))
    gross = abs(rng.gauss(0, 6.0))                    # base micro-spread, mean ~5 bps
    if rng.random() < 0.06:
        gross += rng.uniform(40, 180)                  # occasional dislocation
    size = rng.choices([2_000, 5_000, 10_000, 20_000], weights=[1, 2, 4, 2])[0]
    return {
        "strategy": StrategyType.CROSS_EXCHANGE, "asset": asset,
        "long_ex": long_ex, "short_ex": short_ex,
        "gross_spread_bps": gross, "funding_apr": 0.0,
        "size": float(size), "min_hold": rng.uniform(4, 8),
    }


def gen_funding(rng: random.Random, hold_hours: float) -> dict:
    """Funding-rate carry over a given hold horizon. APR clustered low + spike tail."""
    short_ex = rng.choice(list(PERP_VENUES) + ["kraken", "binance.us"])  # perp leg
    long_ex = rng.choice([e for e in EXCHANGES if e != short_ex])
    asset = rng.choice(list(ASSET_PRICES))
    apr = abs(rng.gauss(0, 15.0))                      # typical funding, mean ~12% APR
    if rng.random() < 0.08:
        apr += rng.uniform(40, 250)                    # funding spike
    size = rng.choices([2_000, 5_000, 10_000], weights=[1, 2, 3])[0]
    return {
        "strategy": StrategyType.FUNDING_RATE_ARB, "asset": asset,
        "long_ex": long_ex, "short_ex": short_ex,
        "gross_spread_bps": 0.0, "funding_apr": apr,
        "size": float(size), "min_hold": hold_hours,
    }


def net_edge_bps(sc: dict) -> tuple[float, bool]:
    """Entry decision via the canonical calculator (same inputs the scorer uses).

    Funding is credited over the planned hold (funding_per_period * periods),
    matching the multi-period carry model in the scorer/strategy.
    """
    slip = estimate_size_tier_bps(sc["size"])
    period_h = _period_hours(sc["short_ex"])
    rate_per_period = (sc["funding_apr"] / 100.0) * (period_h / HOURS_PER_YEAR)
    funding_bps_per_period = rate_per_period * 10_000.0
    periods = max(1, int(sc["min_hold"] // period_h))
    r = calculate_net_edge(
        gross_spread_bps=sc["gross_spread_bps"],
        long_exchange_taker_fee_bps=taker_fee_bps(sc["long_ex"]),
        short_exchange_taker_fee_bps=taker_fee_bps(sc["short_ex"]),
        slippage_long_bps=slip, slippage_short_bps=slip,
        funding_rate_bps_per_period=funding_bps_per_period, funding_periods=periods,
    )
    return r["net_edge_bps"], r["is_viable"]


def realized_pnl_bps(sc: dict) -> float:
    """Realized net PnL (bps of notional) from the production simulator."""
    price = ASSET_PRICES[sc["asset"]]
    opp = Opportunity(
        id="stress", strategy=sc["strategy"], asset=sc["asset"],
        long_exchange=sc["long_ex"], short_exchange=sc["short_ex"],
        gross_spread_bps=sc["gross_spread_bps"], trading_fees_bps=0.0,
        slippage_estimate_bps=0.0, funding_rate_annualized_pct=sc["funding_apr"],
        net_edge_bps=0.0, confidence_score=0.7, recommended_size_usd=sc["size"],
        min_hold_hours=sc["min_hold"], detected_at=datetime(2026, 1, 1), execute=True,
    )
    req = SimulateRequest(
        opportunity=opp, long_reference_price=price, short_reference_price=price,
        long_book_depth_usd=500_000.0, short_book_depth_usd=500_000.0,
    )
    trade = paper_trader._simulate_internal(req)
    return trade.net_pnl_usd / sc["size"] * 10_000.0  # bps of notional


def summarize(name: str, pnls_bps: list[float]) -> None:
    if not pnls_bps:
        print(f"  {name:<22} (no trades)")
        return
    wins = sum(1 for p in pnls_bps if p > 0)
    mean = statistics.mean(pnls_bps)
    med = statistics.median(pnls_bps)
    sp = sorted(pnls_bps)
    p5 = sp[int(0.05 * len(sp))]
    p95 = sp[min(int(0.95 * len(sp)), len(sp) - 1)]
    print(f"  {name:<22} n={len(pnls_bps):<6} win={wins/len(pnls_bps):5.1%}  "
          f"mean={mean:+7.2f}bps  median={med:+7.2f}bps  p5={p5:+7.1f}  p95={p95:+7.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30_000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    def run(label: str, gen, total: int) -> None:
        viable_pnl, viable_edge, all_pnl, viable_n = [], [], [], 0
        for _ in range(total):
            sc = gen(rng)
            edge, is_viable = net_edge_bps(sc)
            pnl_bps = realized_pnl_bps(sc)
            all_pnl.append(pnl_bps)               # if we took EVERYTHING (no bar)
            if is_viable:                          # what the pipeline would actually trade
                viable_n += 1
                viable_pnl.append(pnl_bps)
                viable_edge.append(edge)
        print(f"\n=== {label} — {total:,} scenarios ===")
        print(f"  viable (clears net-edge bar): {viable_n:,} ({viable_n/total:.1%})")
        summarize("TRADED (viable only)", viable_pnl)
        summarize("if we took ALL", all_pnl)
        if viable_edge:
            print(f"  modeled net-edge of traded:  mean {statistics.mean(viable_edge):+.1f} bps  "
                  f"vs realized mean {statistics.mean(viable_pnl):+.1f} bps  "
                  f"(erosion {statistics.mean(viable_edge) - statistics.mean(viable_pnl):+.1f} bps)")
        print(f"  EV per screened scenario (bar ON):  {sum(viable_pnl)/total:+.3f} bps")
        print(f"  EV per scenario (bar OFF, take all): {sum(all_pnl)/total:+.3f} bps")

    run("cross_exchange (spread)", gen_cross_exchange, args.n)

    # Funding carry is highly sensitive to the assumed hold horizon — sweep it
    # rather than reporting a single cherry-picked number.
    print("\n--- funding-rate carry: sensitivity to assumed hold horizon ---")
    for hold in (24.0, 72.0, 168.0, 336.0):
        run(f"funding_arb (hold={hold:.0f}h ≈ {hold/24:.1f}d)",
            lambda r, h=hold: gen_funding(r, h), args.n)


if __name__ == "__main__":
    main()
