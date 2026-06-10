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

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

import position_tracker
from decision_log import build_risk_decision
from shared.tenant import DEFAULT_TENANT
from models import (
    EvaluateRequest,
    EvaluateResponse,
    KillSwitchResetRequest,
    KillSwitchTriggerRequest,
)
from shared.models.opportunity import Opportunity
from shared.models.trade import Trade
from shared.pubsub.publisher import Topic, get_publisher, pubsub_project_id
from rules.capital_validator import validate_capital
from rules.drawdown_guard import DEFAULT_LIMITS, check_drawdown
from rules.exchange_health import check_exchange_health
from rules.kill_switch import (
    clear_kill_switch as kill_switch_reset,
    is_kill_switch_active,
    trigger_kill_switch as kill_switch_trigger,
)
from rules.liquidation_monitor import check_liquidations, scan as scan_liquidations
from rules.directional_budget import check_directional_budget
from rules.eligibility_gate import check_eligibility
from rules.position_limits import check_position_limits
from rules.sentiment_gate import check_sentiment
from state import (
    clear_platform_kill_switch,
    get_redis,
    is_platform_kill_switch_active,
    load_state,
    reset_daily_pnl,
    set_platform_kill_switch,
)

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
        # Route the fill to its owning tenant's risk state.
        position_tracker.apply_trade(get_redis(), trade, trade.user_id)
        message.ack()
    except Exception:
        logger.exception("failed to apply trade fill to risk state")
        message.nack()


_publisher = None


def _get_publisher():
    global _publisher
    if _publisher is None:
        _publisher = get_publisher()
    return _publisher


def _decide(opp: Opportunity) -> tuple[EvaluateResponse, Opportunity | None]:
    """Evaluate an opportunity EXACTLY ONCE and return (result, approved-copy|None).

    Evaluation has side effects (a drawdown breach trips the kill switch *inside*
    ``evaluate``), so callers must never double-evaluate. The approved copy is
    marked ``execute=True`` ready for APPROVED_OPPORTUNITIES."""
    result = evaluate(EvaluateRequest(opportunity=opp, tenant_id=opp.user_id))
    approved = opp.model_copy(update={"execute": True}) if result.approved else None
    return result, approved


def evaluate_and_forward(opp: Opportunity) -> Opportunity | None:
    """Run the full risk evaluation for an opportunity; if approved, return a copy
    marked ``execute=True`` ready to publish to APPROVED_OPPORTUNITIES. This is the
    approve→execute bridge — the ONLY path that turns a detected opportunity into
    an executable one."""
    _, approved = _decide(opp)
    return approved


def _publish_decision(opp: Opportunity, result: EvaluateResponse, ranking: dict | None) -> None:
    """Emit the RiskDecision fact (AI Phase A). FAIL-OPEN: instrumentation must
    never break the choke point, so any error here is logged and swallowed."""
    try:
        decision = build_risk_decision(opp, result, ranking)
        _get_publisher().publish(
            Topic.RISK_DECISIONS, decision, attributes={"tenant": opp.user_id}
        )
    except Exception:
        logger.warning("risk-decision instrumentation failed for opp=%s (non-fatal)", opp.id)


def _advisory_rank(opp: Opportunity) -> dict | None:
    """Call the advisory opportunity-ranker. ADVISORY only and FAIL-OPEN: any
    failure (or no ranker configured) returns None and never blocks the approval —
    the deterministic gate has already decided."""
    url = os.environ.get("OPPORTUNITY_RANKER_URL")
    if not url:
        return None
    try:
        import httpx

        r = httpx.post(
            f"{url.rstrip('/')}/score",
            json={"net_edge_bps": opp.net_edge_bps, "size_usd": opp.recommended_size_usd},
            timeout=1.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        logger.warning("advisory rank unavailable for opp=%s (fail-open)", opp.id)
        return None


def _on_opportunity(message) -> None:
    """Consume a detected opportunity, gate it, and forward approved ones."""
    try:
        opp = Opportunity.model_validate_json(message.data)
    except Exception:
        logger.exception("failed to parse opportunity")
        message.ack()  # malformed — don't redeliver forever
        return
    try:
        result, approved = _decide(opp)  # evaluate ONCE (side-effecting)
        ranking = None
        if approved is not None:
            attrs = {"tenant": opp.user_id}
            ranking = _advisory_rank(approved)  # advisory annotation, never gates
            if ranking:
                attrs["rank_action"] = str(ranking.get("recommended_action", ""))
                attrs["rank_prob"] = str(ranking.get("profitability_probability", ""))
            _get_publisher().publish(Topic.APPROVED_OPPORTUNITIES, approved, attributes=attrs)
            logger.info("opportunity id=%s tenant=%s APPROVED -> arb-approved%s",
                        opp.id, opp.user_id,
                        f" (rank={attrs.get('rank_action')})" if ranking else "")
        else:
            logger.info("opportunity id=%s tenant=%s rejected — not forwarded", opp.id, opp.user_id)
        _publish_decision(opp, result, ranking)  # AI Phase A fact (fail-open)
        message.ack()
    except Exception:
        logger.exception("failed to evaluate/forward opportunity id=%s", getattr(opp, "id", "?"))
        message.nack()


def _start_subscribers() -> None:
    """Subscribe to arb-trade-fills so risk state tracks realized PnL/exposure.

    Skipped without Pub/Sub (local mode) — drive state via the
    POST /positions/apply-fill endpoint instead, matching the repo convention.
    """
    global _subscriber_client
    project_id = pubsub_project_id()
    if not project_id:
        logger.warning("Pub/Sub disabled — risk-engine running without trade-fill subscriber")
        return
    from google.cloud import pubsub_v1

    _subscriber_client = pubsub_v1.SubscriberClient()
    sub = os.environ.get("TRADE_FILLS_SUBSCRIPTION", "arb-trade-fills-risk-engine")
    path = _subscriber_client.subscription_path(project_id, sub)
    _subscriber_futures.append(_subscriber_client.subscribe(path, callback=_on_trade_fill))
    logger.info("subscribed: %s", path)

    # Approve→execute bridge: consume detected opportunities, gate them, and
    # re-emit approved ones to APPROVED_OPPORTUNITIES for the executors.
    opp_sub = os.environ.get("OPPORTUNITIES_SUBSCRIPTION", "arb-opportunities-risk-engine")
    opp_path = _subscriber_client.subscription_path(project_id, opp_sub)
    _subscriber_futures.append(_subscriber_client.subscribe(opp_path, callback=_on_opportunity))
    logger.info("subscribed: %s", opp_path)


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


# When true, a non-default tenant with NO eligibility snapshot is rejected (full
# onboarding enforcement). Default false keeps legacy multi-tenant flows working;
# a snapshot that says not-ready always blocks regardless.
_REQUIRE_ELIGIBILITY = os.environ.get("REQUIRE_TENANT_ELIGIBILITY", "false").lower() == "true"


@app.post("/evaluate", response_model=EvaluateResponse)
def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    redis = get_redis()
    tenant = req.tenant_id

    # Stage 1: kill switch short-circuits everything (platform OR this tenant).
    if is_kill_switch_active(redis, tenant):
        logger.info(
            "opportunity rejected id=%s tenant=%s reason=kill_switch_active",
            req.opportunity.id, tenant,
        )
        return EvaluateResponse(approved=False, violations=[], kill_switch_active=True)

    # Stage 1.5: tenant eligibility (control-plane seam). Short-circuit BEFORE
    # load_state — an un-onboarded tenant has no risk state to load. Skipped for
    # the default tenant; enforced for unknown tenants only when REQUIRE set.
    eligibility_violations = check_eligibility(redis, tenant, require=_REQUIRE_ELIGIBILITY)
    if eligibility_violations:
        logger.info("opportunity rejected id=%s tenant=%s reason=not_eligible",
                    req.opportunity.id, tenant)
        return EvaluateResponse(approved=False, violations=eligibility_violations, kill_switch_active=False)

    state = load_state(redis, tenant)

    # Stage 2: position limits (size, exchange/asset exposure, leverage, min edge).
    violations = []
    violations.extend(check_position_limits(req.opportunity, state, DEFAULT_LIMITS))

    # Stage 2.5: hybrid directional-sleeve budget (neutral opps pass through).
    violations.extend(check_directional_budget(req.opportunity, state, DEFAULT_LIMITS))

    # Stage 3: drawdown — a breach trips the tenant's kill switch synchronously,
    # so downstream callers see kill_switch_active=True on the very next call.
    drawdown_violation = check_drawdown(state, DEFAULT_LIMITS)
    if drawdown_violation:
        violations.append(drawdown_violation)
        kill_switch_trigger(redis, "risk-engine", drawdown_violation.message, tenant)

    # Stage 4: liquidation distance on any open perp. Critical → kill switch.
    liq_violations, liq_checks = check_liquidations(redis)
    violations.extend(liq_violations)
    critical_liqs = [c for c in liq_checks if c.severity == "critical"]
    if critical_liqs:
        # Use the first critical position for the kill reason — operators can
        # see the rest in /liquidations.
        c = critical_liqs[0]
        kill_switch_trigger(redis, "risk-engine", c.message(), tenant)

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
        kill_switch_active=is_kill_switch_active(redis, tenant),
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
def apply_fill(trade: Trade, tenant_id: str = Query(default=DEFAULT_TENANT)) -> dict:
    """Fold a Trade fill into a tenant's risk state.

    Same code path as the arb-trade-fills subscriber — exposed so local/dev
    runs (and tests) can drive risk state without Pub/Sub, and so ops can
    replay a fill manually if needed.
    """
    redis = get_redis()
    position_tracker.apply_trade(redis, trade, tenant_id)
    return load_state(redis, tenant_id).model_dump(mode="json")


@app.post("/positions/reset-daily")
def positions_reset_daily(tenant_id: str = Query(default=DEFAULT_TENANT)) -> dict:
    """Reset a tenant's risk:daily_pnl to 0 — intended for a UTC-midnight scheduler.

    Without a periodic reset the drawdown guard's daily-loss window would
    accumulate forever. The Cloud Scheduler job that calls this is defined in
    infra (see deploy notes).
    """
    redis = get_redis()
    reset_daily_pnl(redis, tenant_id)
    return load_state(redis, tenant_id).model_dump(mode="json")


@app.post("/admin/platform-kill-switch/trigger")
def platform_kill_trigger(req: KillSwitchTriggerRequest) -> dict:
    """Trip the PLATFORM-wide kill switch — halts trading for EVERY tenant.

    The big red button (FRD ADMIN-01). Independent of any tenant's own switch.
    """
    redis = get_redis()
    set_platform_kill_switch(redis, req.triggered_by, req.reason)
    return {"platform_kill_switch_active": True, "reason": req.reason}


@app.post("/admin/platform-kill-switch/reset")
def platform_kill_reset(req: KillSwitchResetRequest) -> dict:
    """Clear the platform-wide kill switch. Requires KILL_SWITCH_RESET_TOKEN."""
    import os
    expected = os.environ.get("KILL_SWITCH_RESET_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="KILL_SWITCH_RESET_TOKEN unset")
    if not req.auth_token or req.auth_token != expected:
        raise HTTPException(status_code=403, detail="invalid auth_token")
    redis = get_redis()
    clear_platform_kill_switch(redis)
    return {"platform_kill_switch_active": is_platform_kill_switch_active(redis)}
