"""opportunity-engine — subscribes to ticks + funding rates, emits opportunities.

The hot path:
  1. Pub/Sub message arrives → MarketSnapshot is updated
  2. Throttled re-evaluation (max once per `EVAL_INTERVAL_S`)
  3. Each strategy runs against the snapshot
  4. Scorer wraps candidates into Opportunity records
  5. Above-threshold opportunities published to `arb-opportunities`

Throttling avoids re-running every strategy on every tick (which would be
~thousands of evaluations per second). The risk-engine is the choke point —
we publish freely and let it decide.

The publish threshold is resolved each evaluation cycle via
``shared.utils.fee_calculator.min_viable_net_edge_bps()`` which honors
a Redis-backed dynamic override at ``opportunity:dynamic_min_spread``.
The env-var ``PUBLISH_NET_EDGE_THRESHOLD_BPS`` is treated as a hard floor:
the publisher applies ``max(env_floor, dynamic)`` so an operator can
temporarily raise the bar without code changes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from directional import opportunity_from_signal
from models import EngineStatus, MarketSnapshot
from scorer import score
from shared.models.exchange_tick import ExchangeTick
from shared.models.funding_rate import FundingRate
from shared.pubsub.publisher import Topic, get_publisher, pubsub_project_id
from shared.utils.fee_calculator import MIN_VIABLE_NET_EDGE_BPS, min_viable_net_edge_bps
from shared.utils.fee_tracker import refresh_all
from strategies import (
    CrossExchangeStrategy,
    FundingRateArbStrategy,
    SpotPerpBasisStrategy,
    Strategy,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("opportunity-engine")


_EVAL_INTERVAL_S = float(os.environ.get("EVAL_INTERVAL_S", "1.0"))
_PUBLISH_NET_EDGE_FLOOR = float(
    os.environ.get("PUBLISH_NET_EDGE_THRESHOLD_BPS", str(MIN_VIABLE_NET_EDGE_BPS))
)

snapshot = MarketSnapshot()
_status_lock = threading.Lock()
_status = EngineStatus(
    status="ok",
    config={
        "eval_interval_s": _EVAL_INTERVAL_S,
        "publish_net_edge_floor_bps": _PUBLISH_NET_EDGE_FLOOR,
        "static_min_viable_net_edge_bps": MIN_VIABLE_NET_EDGE_BPS,
    },
)
_strategies: list[Strategy] = [
    CrossExchangeStrategy(),
    SpotPerpBasisStrategy(),
    FundingRateArbStrategy(),
]
_last_eval_at: float = 0.0
_last_eval_lock = threading.Lock()


def _on_tick(tick: ExchangeTick) -> None:
    snapshot.update_tick(tick)
    _maybe_evaluate()


def _on_funding(rate: FundingRate) -> None:
    snapshot.update_funding(rate)
    _maybe_evaluate()


def _resolve_publish_threshold() -> float:
    """Higher of the env-var floor and the Redis-backed dynamic value."""
    return max(_PUBLISH_NET_EDGE_FLOOR, min_viable_net_edge_bps())


def _maybe_evaluate() -> None:
    global _last_eval_at
    with _last_eval_lock:
        now = time.time()
        if now - _last_eval_at < _EVAL_INTERVAL_S:
            return
        _last_eval_at = now

    publisher = get_publisher()
    threshold = _resolve_publish_threshold()
    candidates = []
    for s in _strategies:
        try:
            candidates.extend(s.evaluate(snapshot))
        except Exception:
            logger.exception("strategy %s crashed during evaluate", s.name)

    published = 0
    for c in candidates:
        opportunity = score(c)
        if opportunity.net_edge_bps < threshold:
            continue
        publisher.publish(
            Topic.OPPORTUNITIES,
            opportunity,
            attributes={
                "strategy": opportunity.strategy.value,
                "asset": opportunity.asset,
            },
        )
        published += 1

    with _status_lock:
        _status.last_evaluation_at = datetime.utcnow()
        _status.last_publish_threshold_bps = threshold
        if published:
            _status.last_publish_at = datetime.utcnow()
            _status.opportunities_published_total += published
        _status.ticks_tracked = len(snapshot._ticks)  # noqa: SLF001 — read-only stats peek
        _status.funding_rates_tracked = len(snapshot._funding)  # noqa: SLF001


_subscriber = None


def _start_subscriber() -> None:
    global _subscriber
    project_id = pubsub_project_id()
    if not project_id:
        logger.warning("Pub/Sub disabled — running without subscriber (local mode)")
        return
    from subscriber import PubSubSubscriber

    _subscriber = PubSubSubscriber(project_id=project_id)
    market_sub = os.environ.get("MARKET_DATA_SUBSCRIPTION", "arb-market-data-opp-engine")
    funding_sub = os.environ.get("FUNDING_RATES_SUBSCRIPTION", "arb-funding-rates-opp-engine")
    _subscriber.subscribe(market_sub, ExchangeTick, _on_tick)
    _subscriber.subscribe(funding_sub, FundingRate, _on_funding)


def _stop_subscriber() -> None:
    if _subscriber is not None:
        _subscriber.stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_subscriber()
    try:
        yield
    finally:
        _stop_subscriber()


app = FastAPI(title="opportunity-engine", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status", response_model=EngineStatus)
def status() -> EngineStatus:
    with _status_lock:
        return _status.model_copy()


class SignalIn(BaseModel):
    """A movement signal in signal-engine's output schema (hybrid plane)."""
    symbol: str
    direction: str
    gross_edge_bps: float
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    expiry_ms: int = Field(default=0, ge=0)
    # The journal key from signal-engine's /detect response. When present it
    # becomes the Opportunity id, so arb_ml.signals joins deterministically to
    # arb_ml.risk_decisions.opportunity_id and on to realized trades — the whole
    # point of journaling. Omitted → a fresh uuid, exactly as before.
    signal_id: str | None = None
    venue: str = "hyperliquid"
    size_usd: float = Field(default=10_000.0, gt=0.0)


@app.post("/signal")
def ingest_signal(sig: SignalIn) -> dict:
    """Ingest a signal-engine movement signal: convert it to a DIRECTIONAL
    Opportunity (directional.py) and publish it into the normal risk-gated flow.

    Same publish threshold as the neutral strategies, and the risk-engine remains
    the only gate — the directional sleeve budget is 0 by default, so published
    directional opportunities are REFUSED until an operator opens the sleeve.
    Fail loud on a bad direction or unknown venue (422), never a silent default."""
    try:
        opp = opportunity_from_signal(
            sig.model_dump(exclude={"venue", "size_usd", "signal_id"}),
            venue=sig.venue, size_usd=sig.size_usd,
        )
    except KeyError as e:
        raise HTTPException(status_code=422, detail=f"unknown venue: {e}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if sig.signal_id:
        opp.id = sig.signal_id  # detection → decision → realized-outcome join key

    threshold = _resolve_publish_threshold()
    published = opp.net_edge_bps >= threshold
    if published:
        get_publisher().publish(
            Topic.OPPORTUNITIES, opp,
            attributes={"strategy": opp.strategy.value, "asset": opp.asset},
        )
        with _status_lock:
            _status.last_publish_at = datetime.utcnow()
            _status.opportunities_published_total += 1
    return {"opportunity_id": opp.id, "net_edge_bps": round(opp.net_edge_bps, 2),
            "threshold_bps": threshold, "published": published}


@app.post("/evaluate-now", response_model=EngineStatus)
def evaluate_now() -> EngineStatus:
    """Force an immediate evaluation — useful for local testing & ops drills."""
    global _last_eval_at
    with _last_eval_lock:
        _last_eval_at = 0.0
    _maybe_evaluate()
    return status()


@app.post("/fee-tier/refresh")
def fee_tier_refresh() -> dict:
    """Pull 30-day volume per exchange from Redis, write the active tier + days
    to next tier, and refresh the dynamic ``MIN_VIABLE_NET_EDGE_BPS`` value.

    Intended to be hit by Cloud Scheduler every 24h. Returns a JSON summary
    of the active tier per exchange so the dashboard can render progress.
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        raise HTTPException(
            status_code=503, detail="REDIS_URL unset — fee tier refresh unavailable"
        )
    from redis import Redis  # local import keeps test envs lightweight

    redis = Redis.from_url(redis_url, decode_responses=True)
    lookups = refresh_all(redis)
    return {
        "tiers": {
            ex: {
                "current_fee_bps": lk.current_fee_bps,
                "current_tier_min_volume_usd": lk.current_tier_min_volume_usd,
                "next_tier_min_volume_usd": lk.next_tier_min_volume_usd,
                "next_tier_fee_bps": lk.next_tier_fee_bps,
            }
            for ex, lk in lookups.items()
        }
    }


# Used by tests and the /evaluate-now endpoint.
def evaluate_once(now: datetime | None = None) -> list:
    """Run all strategies against the current snapshot; return scored opportunities."""
    out = []
    for s in _strategies:
        for cand in s.evaluate(snapshot):
            out.append(score(cand, now=now))
    return out
