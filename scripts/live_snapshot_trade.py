#!/usr/bin/env python3
"""Run paper trades on a LIVE market snapshot (public data, no API keys).

Pulls real spot prices across venues and real funding rates, builds opportunities
from the ACTUAL cross-venue spreads / funding, runs them through the real
net-edge gate (multi-period carry + persistence haircut + 72h cap), and simulates
the viable ones on the real prices via the paper-trader simulator.

This is the honest test the synthetic stress harness can't be: real markets rarely
offer arb that clears a conservative bar, so expect FEW or ZERO viable trades.

Run:  PYTHONPATH=.:services/paper-trader python3 scripts/live_snapshot_trade.py
"""
from __future__ import annotations

import logging
from datetime import datetime

logging.disable(logging.CRITICAL)

import ccxt  # noqa: E402

from shared.models.opportunity import Opportunity, StrategyType  # noqa: E402
from shared.utils.fee_calculator import calculate_net_edge, taker_fee_bps  # noqa: E402
from shared.utils.slippage_model import estimate_size_tier_bps  # noqa: E402

import main as paper_trader  # noqa: E402
from models import SimulateRequest  # noqa: E402

SPOT_VENUES = {"kraken": "kraken", "coinbase": "coinbase", "crypto.com": "cryptocom"}
ASSETS = ["BTC", "ETH", "SOL"]
SIZE_USD = 10_000.0
HOLD_HOURS = 72.0
FUNDING_PERSISTENCE = 0.6
HOURS_PER_YEAR = 365 * 24


def fetch_spot() -> dict:
    """{asset: {venue: (bid, ask)}} from live public tickers."""
    book: dict = {a: {} for a in ASSETS}
    for venue, ccxt_id in SPOT_VENUES.items():
        ex = getattr(ccxt, ccxt_id)({"timeout": 8000, "enableRateLimit": True})
        for a in ASSETS:
            try:
                t = ex.fetch_ticker(f"{a}/USD")
                if t.get("bid") and t.get("ask"):
                    book[a][venue] = (float(t["bid"]), float(t["ask"]))
            except Exception:
                pass
    return book


def fetch_funding() -> dict:
    """{asset: (annualized_pct, period_hours)} from hyperliquid live funding."""
    out: dict = {}
    ex = ccxt.hyperliquid({"timeout": 8000, "enableRateLimit": True})
    for a in ASSETS:
        try:
            fr = ex.fetch_funding_rate(f"{a}/USDC:USDC")
            rate = fr.get("fundingRate")          # per 1h period on HL
            if rate is not None:
                out[a] = (float(rate) * HOURS_PER_YEAR * 100.0, 1.0)
        except Exception:
            pass
    return out


def gate(gross_bps, long_ex, short_ex, funding_apr, period_h):
    slip = estimate_size_tier_bps(SIZE_USD)
    rate_pp = (funding_apr / 100.0) * (period_h / HOURS_PER_YEAR)
    periods = max(1, int(HOLD_HOURS // period_h))
    r = calculate_net_edge(
        gross_spread_bps=gross_bps,
        long_exchange_taker_fee_bps=taker_fee_bps(long_ex),
        short_exchange_taker_fee_bps=taker_fee_bps(short_ex),
        slippage_long_bps=slip, slippage_short_bps=slip,
        funding_rate_bps_per_period=rate_pp * 10_000.0,
        funding_periods=periods, funding_persistence=FUNDING_PERSISTENCE,
    )
    return r["net_edge_bps"], r["is_viable"]


def simulate(strategy, asset, long_ex, short_ex, price, gross_bps, funding_apr):
    opp = Opportunity(
        id="live", strategy=strategy, asset=asset, long_exchange=long_ex,
        short_exchange=short_ex, gross_spread_bps=gross_bps, trading_fees_bps=0.0,
        slippage_estimate_bps=0.0, funding_rate_annualized_pct=funding_apr,
        net_edge_bps=0.0, confidence_score=0.7, recommended_size_usd=SIZE_USD,
        min_hold_hours=HOLD_HOURS, detected_at=datetime(2026, 1, 1), execute=True,
    )
    req = SimulateRequest(opportunity=opp, long_reference_price=price,
                          short_reference_price=price, long_book_depth_usd=500_000.0,
                          short_book_depth_usd=500_000.0)
    return paper_trader._simulate_internal(req).net_pnl_usd


def main() -> None:
    print("Fetching live market snapshot (public endpoints, no keys)...\n")
    spot, funding = fetch_spot(), fetch_funding()

    print("LIVE CROSS-EXCHANGE SPREADS (best buy vs best sell across venues):")
    cross_ops = []
    for a in ASSETS:
        venues = spot[a]
        if len(venues) < 2:
            continue
        long_ex = min(venues, key=lambda v: venues[v][1])   # cheapest ask = buy
        short_ex = max(venues, key=lambda v: venues[v][0])  # highest bid = sell
        buy, sell = venues[long_ex][1], venues[short_ex][0]
        gross = (sell - buy) / buy * 10_000.0
        edge, viable = gate(gross, long_ex, short_ex, 0.0, 8.0)
        flag = "  ✓ VIABLE" if viable else ""
        print(f"  {a:4} buy {long_ex}@{buy:,.2f}  sell {short_ex}@{sell:,.2f}  "
              f"spread {gross:+6.1f}bps  net_edge {edge:+7.1f}bps{flag}")
        if viable:
            cross_ops.append((a, long_ex, short_ex, buy, gross))

    print("\nLIVE FUNDING RATES (hyperliquid perp, annualized):")
    fund_ops = []
    for a in ASSETS:
        if a not in funding:
            continue
        apr, ph = funding[a]
        long_ex = min(spot[a], key=lambda v: spot[a][v][1]) if spot.get(a) else "kraken"
        edge, viable = gate(0.0, long_ex, "hyperliquid", apr, ph)
        flag = "  ✓ VIABLE" if viable else ""
        print(f"  {a:4} funding {apr:+7.2f}% APR  (long {long_ex} / short hyperliquid)  "
              f"net_edge {edge:+7.1f}bps{flag}")
        if viable:
            price = spot[a][long_ex][1]
            fund_ops.append((a, long_ex, "hyperliquid", price, apr))

    print("\n" + "=" * 60)
    viable = [("cross", *o, 0.0) for o in cross_ops] + \
             [("fund", a, le, se, pr, 0.0, apr) for (a, le, se, pr, apr) in fund_ops]
    if not viable:
        print("NO opportunities clear the net-edge bar on this live snapshot.")
        print("That is the honest, expected result — real cross-venue spreads")
        print("(~5-15 bps) and typical funding don't beat ~38 bps round-trip cost")
        print("plus the 50 bps viability bar. Edge appears only in dislocations.")
        return

    print(f"SIMULATING {len(viable)} VIABLE LIVE TRADE(S):")
    total = 0.0
    for v in viable:
        if v[0] == "cross":
            _, a, le, se, price, gross = v
            pnl = simulate(StrategyType.CROSS_EXCHANGE, a, le, se, price, gross, 0.0)
        else:
            _, a, le, se, price, gross, apr = v
            pnl = simulate(StrategyType.FUNDING_RATE_ARB, a, le, se, price, 0.0, apr)
        total += pnl
        print(f"  {a} {le}->{se}: net PnL ${pnl:+,.2f}")
    print(f"\n  TOTAL realized (simulated on live prices): ${total:+,.2f}")


if __name__ == "__main__":
    main()
