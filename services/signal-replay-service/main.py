"""signal-replay-service — label + score signals vs realized outcomes (learning layer)."""
from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel

from replay import ReplayReport, SignalOutcome, label, replay

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("signal-replay-service")
app = FastAPI(title="signal-replay-service", version="0.1.0")


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
