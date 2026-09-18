"""signal-replay-service — label + score signals vs realized outcomes (learning layer).

Two modes, both live in the same ledger (journal.SignalLedger):
  - Manual backtest: POST /replay / /label — score a caller-supplied batch of
    already-paired (signal, outcome) data. Unchanged, still pure.
  - Live join: signal-engine journals every emitted signal to Topic.SIGNALS;
    paper-trader/execution-orchestrator publish fills to Topic.TRADE_FILLS.
    This service subscribes to both (real Pub/Sub) and joins them on
    signal_id == opportunity_id the moment a fill lands, or on expiry via a
    periodic sweep when no fill ever arrives (decayed). /ingest-signal and
    /ingest-fill expose the same join over HTTP for local/no-GCP testing.

Config (env):
  GCP_PROJECT_ID / PUBSUB_EMULATOR_HOST — when neither is set, Pub/Sub is
  disabled (local mode); drive the join via /ingest-signal + /ingest-fill.
  SIGNALS_SUBSCRIPTION (default arb-signals-replay)
  TRADE_FILLS_SUBSCRIPTION (default arb-trade-fills-replay)
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel

from journal import SignalLedger
from replay import ReplayReport, SignalOutcome, label, replay
from shared.models.movement_signal import MovementSignal
from shared.models.trade import Trade
from shared.pubsub.publisher import pubsub_project_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("signal-replay-service")

_ledger = SignalLedger()
_lock = threading.Lock()


def _on_signal(signal: MovementSignal) -> None:
    with _lock:
        _ledger.record_signal(signal)


def _on_fill(trade: Trade) -> None:
    with _lock:
        scored = _ledger.record_fill(trade)
    if scored:
        logger.info("scored signal_id=%s label=%s realized_bps=%.2f",
                    scored.signal_id, scored.label, scored.outcome.realized_bps)


_subscriber = None


def _start_subscriber() -> None:
    global _subscriber
    project_id = pubsub_project_id()
    if not project_id:
        logger.warning("Pub/Sub disabled — running without subscriber (local mode)")
        return
    from subscriber import PubSubSubscriber

    _subscriber = PubSubSubscriber(project_id=project_id)
    signals_sub = os.environ.get("SIGNALS_SUBSCRIPTION", "arb-signals-replay")
    fills_sub = os.environ.get("TRADE_FILLS_SUBSCRIPTION", "arb-trade-fills-replay")
    _subscriber.subscribe(signals_sub, MovementSignal, _on_signal)
    _subscriber.subscribe(fills_sub, Trade, _on_fill)


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


app = FastAPI(title="signal-replay-service", version="0.1.0", lifespan=lifespan)


class OutcomeIn(BaseModel):
    family: str
    regime: str
    direction: str
    gross_edge_bps: float
    realized_bps: float
    reached_execution: bool = True


class ReplayRequest(BaseModel):
    outcomes: list[OutcomeIn]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/replay")
def run_replay(req: ReplayRequest) -> dict:
    outcomes = [SignalOutcome(**o.model_dump()) for o in req.outcomes]
    report: ReplayReport = replay(outcomes)
    return asdict(report)


@app.post("/label")
def label_one(o: OutcomeIn) -> dict:
    return {"label": label(SignalOutcome(**o.model_dump()))}


@app.post("/ingest-signal")
def ingest_signal(signal: MovementSignal) -> dict:
    """Manually feed a journaled signal into the join (local/no-GCP testing —
    mirrors what the Topic.SIGNALS subscriber does in a real deployment)."""
    _on_signal(signal)
    return {"pending_count": _ledger.pending_count}


@app.post("/ingest-fill")
def ingest_fill(trade: Trade) -> dict:
    """Manually feed a trade fill into the join (local/no-GCP testing —
    mirrors what the Topic.TRADE_FILLS subscriber does in a real deployment)."""
    with _lock:
        scored = _ledger.record_fill(trade)
    if scored is None:
        return {"scored": False}
    return {"scored": True, "label": scored.label,
            "realized_bps": round(scored.outcome.realized_bps, 2)}


@app.post("/sweep-expired")
def sweep_expired() -> dict:
    """Score out any pending signal whose expiry window has elapsed with no
    matching fill as 'decayed'. Real deployments should call this on a timer;
    exposed here so tests and ops can trigger it on demand."""
    with _lock:
        swept = _ledger.sweep_expired()
    return {"swept": len(swept), "signal_ids": [s.signal_id for s in swept]}


@app.get("/pending")
def pending() -> dict:
    return {"pending_count": _ledger.pending_count}


@app.get("/report")
def live_report() -> dict:
    """The running ReplayReport over every signal scored so far via the live
    join (record_fill + sweep_expired) — NOT the manual /replay backtest."""
    with _lock:
        outcomes = [s.outcome for s in _ledger.scored]
    return asdict(replay(outcomes))
