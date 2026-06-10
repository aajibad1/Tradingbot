"""agent-evals — eval harness + promotion verdicts for AI versions (docs/10).

Records eval runs for an (agent, prompt_version, model_version) against thresholds
and exposes the latest pass/fail verdict. agent-registry consults the verdict before
it will activate a version — so an un-evaluated or failing version can never go
live. Read/record only; performs no agent action.

Endpoints:
  GET  /healthz
  POST /v1/evals/run            {agent, prompt_version, model_version?, metrics, evaluator}
  GET  /v1/evals?agent=&version=
  GET  /v1/evals/verdict?agent=&version=     latest pass/fail for a version
  GET  /v1/evals/thresholds
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

import evals
from shared.http import APIError, install_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("agent-evals")

PRODUCER = "agent-evals"

# All eval runs (append-only), newest last.
_runs: list[dict] = []

app = FastAPI(title="agent-evals", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvalRun(BaseModel):
    agent: str
    prompt_version: str
    model_version: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict,
                                      description="accuracy, hallucination_rate, latency_ms, ...")
    evaluator: str = Field(default="offline", description="who/what ran the eval")
    thresholds: dict[str, float] | None = None


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/evals/run")
def run_eval(req: EvalRun) -> dict[str, Any]:
    passed, failures = evals.evaluate(req.metrics, req.thresholds)
    record = {
        "id": f"eval_{uuid.uuid4().hex[:16]}",
        "agent": req.agent,
        "prompt_version": req.prompt_version,
        "model_version": req.model_version,
        "metrics": req.metrics,
        "passed": passed,
        "failures": failures,
        "evaluator": req.evaluator,
        "created_at": _now(),
    }
    _runs.append(record)
    logger.info("eval agent=%s version=%s passed=%s", req.agent, req.prompt_version, passed)
    return record


@app.get("/v1/evals")
def list_evals(agent: str | None = None, version: str | None = None) -> dict[str, Any]:
    out = [r for r in _runs
           if (agent is None or r["agent"] == agent) and (version is None or r["prompt_version"] == version)]
    return {"evals": out, "count": len(out)}


@app.get("/v1/evals/verdict")
def verdict(agent: str, version: str) -> dict[str, Any]:
    """Latest eval verdict for a version. 404 if it was never evaluated — which
    the registry treats as 'not promotable'."""
    matching = [r for r in _runs if r["agent"] == agent and r["prompt_version"] == version]
    if not matching:
        raise APIError("not_evaluated", f"{agent} {version} has no eval runs", http_status=404)
    latest = matching[-1]
    return {"agent": agent, "prompt_version": version, "passed": latest["passed"],
            "failures": latest["failures"], "eval_id": latest["id"], "evaluated_at": latest["created_at"]}


@app.get("/v1/evals/thresholds")
def thresholds() -> dict[str, Any]:
    return {"thresholds": evals.DEFAULT_THRESHOLDS}
