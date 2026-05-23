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
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI

from models import EngineStatus, MarketSnapshot
from scorer import score
from shared.models.exchange_tick import ExchangeTick
from shared.models.funding_rate import FundingRate
from shared.pubsub.publisher import Topic, get_publisher
from shared.utils.fee_calculator import MIN_VIABLE_NET_EDGE_BPS
from strategies import (
    CrossExchangeStrategy,
    FundingRateArbStrategy,
    SpotPerpBasisStrategy,
    Strategy,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("opportunity-engine")


_EVAL_INTERVAL_S = float(os.environ.get("EVAL_INTERVAL_S", "1.0"))
_PUBLISH_NET_EDGE_THRESHOLD = float(
    os.environ.get("PUBLISH_NET_EDGE_THRESHOLD_BPS", str(MIN_VIABLE_NET_EDGE_BPS))
)

snapshot = MarketSnapshot()
_status_lock = threading.Lock()
_status = EngineStatus(
    status="ok",
    config={
        "eval_interval_s": _EVAL_INTERVAL_S,
        "publish_net_edge_threshold_bps": _PUBLISH_NET_EDGE_THRESHOLD,
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


def _maybe_evaluate() -> None:
    global _last_eval_at
    with _last_eval_lock:
        now = time.time()
        if now - _last_eval_at < _EVAL_INTERVAL_S:
            return
        _last_eval_at = now

    publisher = get_publisher()
    candidates = []
    for s in _strategies:
        try:
            candidates.extend(s.evaluate(snapshot))
        except Exception:
            logger.exception("strategy %s crashed during evaluate", s.name)

    published = 0
    for c in candidates:
        opportunity = score(c)
        if opportunity.net_edge_bps < _PUBLISH_NET_EDGE_THRESHOLD:
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
        if published:
            _status.last_publish_at = datetime.utcnow()
            _status.opportunities_published_total += published
        _status.ticks_tracked = len(snapshot._ticks)  # noqa: SLF001 — read-only stats peek
        _status.funding_rates_tracked = len(snapshot._funding)  # noqa: SLF001


_subscriber = None


def _start_subscriber() -> None:
    global _subscriber
    if not os.environ.get("GCP_PROJECT_ID"):
        logger.warning("GCP_PROJECT_ID unset — running without Pub/Sub subscriber (local mode)")
        return
    from subscriber import PubSubSubscriber

    _subscriber = PubSubSubscriber()
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


@app.post("/evaluate-now", response_model=EngineStatus)
def evaluate_now() -> EngineStatus:
    """Force an immediate evaluation — useful for local testing & ops drills."""
    global _last_eval_at
    with _last_eval_lock:
        _last_eval_at = 0.0
    _maybe_evaluate()
    return status()


# Used by tests and the /evaluate-now endpoint.
def evaluate_once(now: datetime | None = None) -> list:
    """Run all strategies against the current snapshot; return scored opportunities."""
    out = []
    for s in _strategies:
        for cand in s.evaluate(snapshot):
            out.append(score(cand, now=now))
    return out
