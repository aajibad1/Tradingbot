"""Risk engine — FastAPI service.

Endpoints:
  GET  /healthz                      Cloud Run liveness (503 if kill switch tripped)
  GET  /state                        Current risk state snapshot
  GET  /status                       Alias of /state (canonical name in spec)
  GET  /liquidations                 Liquidation-distance scan for all open perps
  GET  /capital-validation           Capital sufficiency snapshot (informational)
  POST /evaluate                     Approve or reject an Opportunity
  POST /kill-switch/trigger          Trip the kill switch
  POST /kill-switch/reset            Reset (requires KILL_SWITCH_RESET_TOKEN +
                                     capital >= MIN_VIABLE_CAPITAL_USD)
  POST /admin/reset                  Alias of /kill-switch/reset

The evaluator is the choke point for every signal flowing downstream. If it
rejects, the Opportunity never reaches paper-trader or execution-orchestrator.

Evaluation order (canonical — see CLAUDE.md "risk-engine is the choke point"):
  1. Kill switch — short-circuit reject, no other rules even run.
  2. Position limits (size, exchange exposure, asset concentration, leverage,
     min net edge).
  3. Drawdown — a breach trips the kill switch SYNCHRONOUSLY inside this
     handler so the same request both reports the violation and locks down
     the system.
  4. Liquidation distance — same synchronous-kill behaviour for any open
     position inside the CRITICAL band.
  5. Sentiment gate — fail-OPEN soft gate; never trips the kill switch.
  6. Exchange health (API latency on both legs).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

import position_tracker
from models import (
    EvaluateRequest,
    EvaluateResponse,
    KillSwitchResetRequest,
    KillSwitchTriggerRequest,
)
from shared.models.trade import Trade
from rules.capital_validator import validate_capital
from rules.drawdown_guard import DEFAULT_LIMITS, check_drawdown
from rules.exchange_health import check_exchange_health
from rules.kill_switch import (
    clear_kill_switch as kill_switch_reset,
    is_kill_switch_active,
    trigger_kill_switch as kill_switch_trigger,
)
from rules.liquidation_monitor import check_liquidations, scan as scan_liquidations
from rules.position_limits import check_position_limits
from rules.sentiment_gate import check_sentiment
from state import get_redis, load_state, reset_daily_pnl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("risk-engine")


_subscriber_client = None
_subscriber_futures: list = []


def _on_trade_fill(message) -> None:
    """Fold an inbound Trade fill into the Redis risk state."""
    try:
        trade = Trade.model_validate_json(message.data)
        position_tracker.apply_trade(get_redis(), trade)
        message.ack()
    except Exception:
        logger.exception("failed to apply trade fill to risk state")
        message.nack()


def _start_subscribers() -> None:
    """Subscribe to arb-trade-fills so risk state tracks realized PnL/exposure.

    Skipped without GCP_PROJECT_ID (local mode) — drive state via the
    POST /positions/apply-fill endpoint instead, matching the repo convention.
    """
    global _subscriber_client
    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        logger.warning("GCP_PROJECT_ID unset — risk-engine running without trade-fill subscriber")
        return
    from google.cloud import pubsub_v1

    _subscriber_client = pubsub_v1.SubscriberClient()
    sub = os.environ.get("TRADE_FILLS_SUBSCRIPTION", "arb-trade-fills-risk-engine")
    path = _subscriber_client.subscription_path(project_id, sub)
    _subscriber_futures.append(_subscriber_client.subscribe(path, callback=_on_trade_fill))
    logger.info("subscribed: %s", path)


def _stop_subscribers() -> None:
    for f in _subscriber_futures:
        f.cancel()
    if _subscriber_client is not None:
        _subscriber_client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_subscribers()
    try:
        yield
    finally:
        _stop_subscribers()


app = FastAPI(title="risk-engine", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness probe. Returns 503 while the kill switch is active so Cloud
    Run / load balancers stop sending traffic to a locked-down instance."""
    try:
        redis = get_redis()
        if is_kill_switch_active(redis):
            return JSONResponse(
                status_code=503,
                content={"status": "kill_switch_active"},
            )
    except Exception:  # noqa: BLE001 — liveness must not hard-fail on transient Redis blips
        logger.exception("healthz redis probe failed")
        return JSONResponse(status_code=503, content={"status": "redis_unavailable"})
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.get("/state")
def get_state() -> dict:
    redis = get_redis()
    return load_state(redis).model_dump(mode="json")


@app.get("/status")
def get_status() -> dict:
    """Alias of /state — returns the full RiskState snapshot."""
    return get_state()


@app.get("/liquidations")
def get_liquidations() -> dict:
    """Return the current liquidation-distance scan for all open perps.

    Read-only — the kill-switch trigger only fires through /evaluate, so
    operators can dashboard this endpoint without side-effects.
    """
    redis = get_redis()
    checks = scan_liquidations(redis)
    return {
        "positions": [
            {
                "opportunity_id": c.opportunity_id,
                "exchange": c.exchange,
                "asset": c.asset,
                "mark_price": c.mark_price,
                "liquidation_price": c.liquidation_price,
                "distance_pct": c.distance_pct,
                "severity": c.severity,
            }
            for c in checks
        ]
    }


@app.get("/capital-validation")
def get_capital_validation() -> dict:
    """Return the capital-validation snapshot.

    Informational — does NOT block opportunities. The kill-switch reset
    endpoint uses the same check to refuse reset under-capitalised systems.
    """
    redis = get_redis()
    state = load_state(redis)
    result = validate_capital(state.capital_usd)
    return {
        "passed": result.passed,
        "capital_usd": result.capital_usd,
        "monthly_infra_cost_usd": result.monthly_infra_cost_usd,
        "expected_monthly_gross_usd": result.expected_monthly_gross_usd,
        "expected_monthly_net_usd": result.expected_monthly_net_usd,
        "min_viable_usd": result.min_viable_usd,
        "recommended_usd": result.recommended_usd,
        "message": result.message,
    }


@app.post("/evaluate", response_model=EvaluateResponse)
def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    redis = get_redis()

    # Stage 1: kill switch short-circuits everything.
    if is_kill_switch_active(redis):
        logger.info(
            "opportunity rejected id=%s reason=kill_switch_active",
            req.opportunity.id,
        )
        return EvaluateResponse(approved=False, violations=[], kill_switch_active=True)

    state = load_state(redis)

    # Stage 2: position limits (size, exchange/asset exposure, leverage, min edge).
    violations = []
    violations.extend(check_position_limits(req.opportunity, state, DEFAULT_LIMITS))

    # Stage 3: drawdown — a breach trips the kill switch synchronously, so
    # downstream callers see kill_switch_active=True on the very next call.
    drawdown_violation = check_drawdown(state, DEFAULT_LIMITS)
    if drawdown_violation:
        violations.append(drawdown_violation)
        kill_switch_trigger(redis, "risk-engine", drawdown_violation.message)

    # Stage 4: liquidation distance on any open perp. Critical → kill switch.
    liq_violations, liq_checks = check_liquidations(redis)
    violations.extend(liq_violations)
    critical_liqs = [c for c in liq_checks if c.severity == "critical"]
    if critical_liqs:
        # Use the first critical position for the kill reason — operators can
        # see the rest in /liquidations.
        c = critical_liqs[0]
        kill_switch_trigger(redis, "risk-engine", c.message())

    # Stage 5: sentiment gate (fail-open soft gate; never trips kill switch).
    # The sentiment-service is the sole writer of sentiment:*. Missing or
    # stale data results in [] here, which intentionally does NOT block.
    violations.extend(check_sentiment(redis))

    # Stage 6: exchange health (both legs must be responsive).
    venues = [req.opportunity.long_exchange, req.opportunity.short_exchange]
    violations.extend(
        check_exchange_health(redis, venues, DEFAULT_LIMITS["max_api_latency_ms"])
    )

    approved = not violations
    if not approved:
        logger.info(
            "opportunity rejected id=%s violations=%s",
            req.opportunity.id,
            [v.rule.value for v in violations],
        )
    else:
        logger.info(
            "opportunity approved id=%s net_edge_bps=%.2f",
            req.opportunity.id,
            req.opportunity.net_edge_bps,
        )
    return EvaluateResponse(
        approved=approved,
        violations=violations,
        kill_switch_active=is_kill_switch_active(redis),
    )


@app.post("/kill-switch/trigger")
def trigger(req: KillSwitchTriggerRequest) -> dict:
    redis = get_redis()
    state = kill_switch_trigger(redis, req.triggered_by, req.reason)
    return state.model_dump(mode="json")


@app.post("/kill-switch/reset")
def reset(req: KillSwitchResetRequest) -> dict:
    redis = get_redis()
    # Capital sufficiency gate — never reset into a money-losing posture.
    state_pre = load_state(redis)
    capital = validate_capital(state_pre.capital_usd)
    if not capital.passed:
        raise HTTPException(status_code=409, detail=capital.message)
    try:
        state = kill_switch_reset(redis, req.auth_token, req.reset_by)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except RuntimeError as e:
        # Misconfigured deployment — token env-var unset.
        raise HTTPException(status_code=500, detail=str(e)) from e
    return state.model_dump(mode="json")


@app.post("/admin/reset")
def admin_reset(req: KillSwitchResetRequest) -> dict:
    """Alias of /kill-switch/reset — matches spec "POST /admin/reset"."""
    return reset(req)


@app.post("/positions/apply-fill")
def apply_fill(trade: Trade) -> dict:
    """Fold a Trade fill into the risk state.

    Same code path as the arb-trade-fills subscriber — exposed so local/dev
    runs (and tests) can drive risk state without Pub/Sub, and so ops can
    replay a fill manually if needed.
    """
    redis = get_redis()
    position_tracker.apply_trade(redis, trade)
    return load_state(redis).model_dump(mode="json")


@app.post("/positions/reset-daily")
def positions_reset_daily() -> dict:
    """Reset risk:daily_pnl_usd to 0 — intended for a UTC-midnight scheduler.

    Without a periodic reset the drawdown guard's daily-loss window would
    accumulate forever. The Cloud Scheduler job that calls this is defined in
    infra (see deploy notes).
    """
    redis = get_redis()
    reset_daily_pnl(redis)
    return load_state(redis).model_dump(mode="json")
