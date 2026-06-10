"""Paper-trader service — FastAPI.

Takes a risk-engine-approved Opportunity, simulates execution with realistic
fees/slippage/partial fills, runs the funding accrual + spread-collapse model,
then publishes a Trade record to `arb-trade-fills` for the ledger to persist.

Endpoints:
  GET  /healthz
  GET  /status
  POST /simulate (manual trigger)

Subscriber:
  arb-opportunities → filters execute=True → simulates → publishes to arb-trade-fills
"""

from __future__ import annotations

import logging
import os
import random
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from pydantic import BaseModel

from ledger import append_paper_trade
from models import SimulateRequest, SimulateResponse
from shared.models.opportunity import Opportunity
from simulator.execution_model import new_trade_id, simulate_execution
from simulator.funding_simulator import funding_period_hours, simulate_funding_payment
from simulator.spread_collapse import sample_early_exit
from shared.models.trade import Trade, TradeStatus, TradeType
from shared.utils.slippage_model import estimate_size_tier_bps

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("paper-trader")

_subscriber_future = None
_trades_simulated = 0
_trades_rejected = 0
_lock = threading.Lock()


def _on_opportunity(message) -> None:
    """Callback for approved opportunities from Pub/Sub."""
    global _trades_simulated, _trades_rejected

    try:
        opp = Opportunity.model_validate_json(message.data)
    except Exception as e:
        logger.error("failed to parse opportunity: %s", e)
        message.ack()
        return

    with _lock:
        if not opp.execute:
            _trades_rejected += 1
            logger.debug("opportunity id=%s rejected: execute=False", opp.id)
            message.ack()
            return
        _trades_simulated += 1

    logger.info("simulating opportunity id=%s strategy=%s net_edge=%.1f bps", opp.id, opp.strategy, opp.net_edge_bps)

    # Use the REAL reference prices carried on the opportunity (set by the
    # detecting strategy). Fall back only if a producer didn't populate them —
    # that should not happen post-fix, so warn loudly rather than silently
    # simulating every asset at a placeholder price.
    long_price = opp.long_reference_price
    short_price = opp.short_reference_price
    if long_price is None or short_price is None:
        logger.warning(
            "opportunity id=%s missing reference price(s) — falling back to placeholder; "
            "PnL for this trade is unreliable", opp.id,
        )
        long_price = long_price or 50_000.0
        short_price = short_price or 50_000.0

    req = SimulateRequest(
        opportunity=opp,
        long_reference_price=long_price,
        short_reference_price=short_price,
        long_book_depth_usd=250_000.0,
        short_book_depth_usd=250_000.0,
    )
    _simulate_internal(req)
    message.ack()


def simulate_trade(
    req: SimulateRequest,
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> Trade | None:
    """Pure simulation — produce a paper ``Trade`` from an approved opportunity.

    No side effects: this does NOT publish to ``arb-trade-fills`` (that is
    ``_simulate_internal``'s job). Injecting ``rng`` / ``now`` makes the
    simulation fully deterministic, which the stage-2 paper-run gate
    (``scripts/paper_run.py``) relies on for a reproducible PASS/FAIL and which
    tests use to pin partial-fill / early-exit behaviour.
    """
    if not req.opportunity.execute:
        logger.warning("opportunity id=%s not approved (execute=False)", req.opportunity.id)
        return None

    rng = rng or random.Random()
    now = now or datetime.utcnow()

    # Simulate the two legs concurrently (logically simultaneous, latency-skewed).
    long_leg = simulate_execution(
        exchange=req.opportunity.long_exchange,
        asset=req.opportunity.asset,
        side="buy",
        size_usd=req.opportunity.recommended_size_usd,
        reference_price=req.long_reference_price,
        book_depth_usd=req.long_book_depth_usd,
        now=now,
        rng=rng,
    )
    short_leg = simulate_execution(
        exchange=req.opportunity.short_exchange,
        asset=req.opportunity.asset,
        side="sell",
        size_usd=req.opportunity.recommended_size_usd,
        reference_price=req.short_reference_price,
        book_depth_usd=req.short_book_depth_usd,
        now=now,
        rng=rng,
    )

    # Hold horizon, possibly shortened by spread collapse.
    planned_hours = max(req.opportunity.min_hold_hours, 1.0)
    early_exit = sample_early_exit(planned_hours, rng=rng)
    actual_hours = early_exit if early_exit is not None else planned_hours
    closed_at = now + timedelta(hours=actual_hours)

    # Funding accrued on the perp (short) leg. Convert annualized pct → per-period pct.
    short_period_hours = funding_period_hours(req.opportunity.short_exchange)
    periods_elapsed = int(actual_hours // short_period_hours)
    funding_pct_per_period = (
        req.opportunity.funding_rate_annualized_pct
        * (short_period_hours / (365 * 24))
    )
    # Partial fills: spread capture and funding accrue only on the HEDGED
    # (delta-neutral) notional = the smaller of the two filled legs. Any
    # imbalance is unhedged directional exposure that must be flattened at a
    # cost — so partial fills correctly reduce PnL instead of being booked at
    # the full requested size.
    long_filled = long_leg.leg.size * long_leg.leg.fill_price
    short_filled = short_leg.leg.size * short_leg.leg.fill_price
    hedged_notional = min(long_filled, short_filled)
    residual_notional = abs(long_filled - short_filled)

    funding_usd = simulate_funding_payment(
        notional_usd=hedged_notional,
        funding_rate_pct_per_period=funding_pct_per_period,
        is_short_perp=True,
        periods=periods_elapsed,
    )

    # Gross PnL = price convergence — assume the spread fully closes at exit
    # when no early exit; otherwise the spread has only half-closed.
    spread_capture_ratio = 0.5 if early_exit is not None else 1.0
    gross_pnl = hedged_notional * (req.opportunity.gross_spread_bps / 10_000.0) * spread_capture_ratio

    fees_usd = long_leg.leg.fee_usd + short_leg.leg.fee_usd
    slippage_usd = long_leg.leg.slippage_usd + short_leg.leg.slippage_usd
    # Cost to flatten the unhedged residual leg (slippage on the stray exposure).
    residual_cost_usd = residual_notional * estimate_size_tier_bps(residual_notional) / 10_000.0
    net_pnl = gross_pnl + funding_usd - fees_usd - slippage_usd - residual_cost_usd

    trade = Trade(
        id=new_trade_id(),
        user_id=req.opportunity.user_id,
        opportunity_id=req.opportunity.id,
        type=TradeType.PAPER,
        legs=[long_leg.leg, short_leg.leg],
        gross_pnl_usd=gross_pnl,
        net_pnl_usd=net_pnl,
        status=TradeStatus.CLOSED,
        opened_at=now,
        closed_at=closed_at,
        funding_collected_usd=funding_usd,
        notes=(
            f"early_exit={early_exit:.1f}h" if early_exit is not None
            else f"held_full_horizon={planned_hours:.1f}h"
        ),
    )
    logger.info("trade id=%s net_pnl=%.2f closed_at=%s", trade.id, net_pnl, closed_at)
    return trade


def _simulate_internal(req: SimulateRequest) -> Trade | None:
    """Simulate then publish to ``arb-trade-fills`` — the Pub/Sub callback and
    POST ``/simulate`` path. Wraps the pure ``simulate_trade``."""
    trade = simulate_trade(req)
    if trade is not None:
        append_paper_trade(trade)
    return trade


def _start_subscriber() -> None:
    global _subscriber_future
    from shared.pubsub.publisher import pubsub_project_id

    project_id = pubsub_project_id()
    if not project_id:
        logger.warning("Pub/Sub disabled — running without subscriber (local mode)")
        return

    from google.cloud import pubsub_v1

    # Consume APPROVED opportunities (arb-approved) — risk-engine only forwards
    # risk-approved opps here with execute=True. Never subscribe to the raw
    # arb-opportunities feed, or unapproved trades would execute.
    subscription_name = os.environ.get("OPPORTUNITIES_SUBSCRIPTION", "arb-approved-paper-trader")

    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(project_id, subscription_name)

    _subscriber_future = subscriber.subscribe(subscription_path, callback=_on_opportunity)
    logger.info("subscribed to %s", subscription_path)


def _stop_subscriber() -> None:
    if _subscriber_future is not None:
        _subscriber_future.cancel()
        logger.info("subscriber stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_subscriber()
    try:
        yield
    finally:
        _stop_subscriber()


app = FastAPI(title="paper-trader", version="0.1.0", lifespan=lifespan)


class StatusResponse(BaseModel):
    trades_simulated: int
    trades_rejected: int


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    with _lock:
        return StatusResponse(
            trades_simulated=_trades_simulated,
            trades_rejected=_trades_rejected,
        )


@app.post("/simulate", response_model=SimulateResponse)
def simulate(req: SimulateRequest) -> SimulateResponse:
    """Manual simulation trigger — useful for testing and ops drills."""
    if not req.opportunity.execute:
        return SimulateResponse(
            trade_id="",
            accepted=False,
            rejection_reason="opportunity not approved by risk-engine (execute=False)",
            opened_at=datetime.utcnow(),
        )

    trade = _simulate_internal(req)
    if trade is None:
        return SimulateResponse(
            trade_id="",
            accepted=False,
            rejection_reason="simulation failed",
            opened_at=datetime.utcnow(),
        )

    return SimulateResponse(
        trade_id=trade.id,
        accepted=True,
        net_pnl_usd=trade.net_pnl_usd,
        gross_pnl_usd=trade.gross_pnl_usd,
        fees_usd=sum(leg.fee_usd for leg in trade.legs),
        slippage_usd=sum(leg.slippage_usd for leg in trade.legs),
        funding_collected_usd=trade.funding_collected_usd,
        opened_at=trade.opened_at,
        closed_at=trade.closed_at,
        notes=trade.notes,
    )
