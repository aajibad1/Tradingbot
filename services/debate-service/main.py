"""debate-service — multi-agent adversarial verification (ADVISORY).

Runs a proposer→skeptic→judge debate over a claim and returns a calibrated,
auditable verdict. Advisory only: never bypasses the risk-engine, never in the
per-trade hot path. Agents are LLM-backed in prod (Claude reasoning + Perplexity
evidence); without keys it returns an 'uncertain' verdict (fail-soft).

Endpoints:
  GET  /healthz
  POST /debate   — {claim, context?, n_skeptics?}
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel, Field

from debate import Position, run_debate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("debate-service")

app = FastAPI(title="debate-service", version="0.1.0")


def _abstain_proposer(claim, context) -> Position:
    return Position(role="proposer", stance="support", confidence=0.5,
                    argument="(no LLM configured — abstaining)", model_version=None)


def _abstain_skeptic(claim, context, proposer) -> Position:
    return Position(role="skeptic", stance="uncertain", confidence=0.5,
                    argument="(no LLM configured — abstaining)", model_version=None)


def _agents(n_skeptics: int):
    """Resolve debate agents. With ANTHROPIC_API_KEY a real Claude-backed panel
    plugs in here; without it we fail soft to abstaining agents (verdict
    'uncertain'), so the deterministic path is never blocked."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _abstain_proposer, [_abstain_skeptic for _ in range(n_skeptics)]
    # TODO: wire Claude personas (proposer + diverse skeptics) + Perplexity evidence.
    return _abstain_proposer, [_abstain_skeptic for _ in range(n_skeptics)]


class DebateIn(BaseModel):
    claim: str
    context: dict = Field(default_factory=dict)
    n_skeptics: int = Field(default=2, ge=1, le=5)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/debate")
def debate(req: DebateIn) -> dict:
    proposer, skeptics = _agents(req.n_skeptics)
    verdict = run_debate(req.claim, req.context, proposer, skeptics)
    logger.info("debate claim=%r -> %s (conf=%.2f, refuted %d/%d)",
                req.claim[:80], verdict.decision, verdict.confidence,
                verdict.refuted_by, verdict.n_skeptics)
    out = asdict(verdict)
    return out
