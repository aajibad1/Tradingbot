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
from shared.a2a import (
    A2AError,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Message,
    base_url,
    data_part,
    install_a2a,
    text_part,
)
from shared.a2a import errors as a2a_errors
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


# --- A2A (Agent2Agent) surface ------------------------------------------------
# The eval harness (docs/10) exposed over A2A: a peer (e.g. an orchestrator
# considering a promotion) asks whether an agent version passed its evals. A
# never-evaluated version resolves to passed=false ('not promotable'), matching
# how agent-registry fail-closes on activation.
_A2A_CARD = AgentCard(
    name="agent-evals",
    description=("Eval harness for agent prompt/model versions (accuracy, "
                 "hallucination, latency). Report the latest pass/fail verdict for a "
                 "version; un-evaluated versions are reported as not-passing."),
    url=f"{base_url('agent-evals')}/a2a",
    version=app.version,
    provider=AgentProvider(organization="traditbot"),
    skills=[AgentSkill(
        id="eval-verdict",
        name="Get eval verdict",
        description=("Given an agent and prompt version, return the latest pass/fail "
                     "verdict and any failure reasons."),
        tags=["governance", "evals", "promotion", "safety"],
        examples=['{"agent":"opportunity-ranker","version":"v3"}'],
    )],
)


def _a2a_handle(message: Message):
    data = message.data() or {}
    agent = (data.get("agent") or "").strip()
    version = (data.get("version") or "").strip()
    if not agent or not version:
        raise A2AError(a2a_errors.INVALID_PARAMS,
                       "data part with 'agent' and 'version' is required")
    try:
        result = verdict(agent, version)
    except APIError as exc:
        if exc.http_status == 404:  # never evaluated → not promotable (fail-closed)
            result = {"agent": agent, "prompt_version": version, "passed": False,
                      "failures": ["not evaluated"], "evaluated_at": None}
        else:
            raise A2AError(a2a_errors.INVALID_PARAMS, f"{exc.code}: {exc.message}") from exc
    summary = f"{agent} {version}: {'PASS' if result['passed'] else 'FAIL'}"
    return [text_part(summary), data_part(result)]


install_a2a(app, card=_A2A_CARD, handler=_a2a_handle)
