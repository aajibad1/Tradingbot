"""corridor-intelligence-service — Perplexity-backed Africa corridor intelligence.

ADVISORY: enriches the corridor-engine's settlement-confidence with researched
corridor reliability / regulatory risk. Never executes or bypasses risk. Perplexity
is key-gated; without a key it serves a deterministic baseline (graceful degrade).

Endpoints:
  GET  /healthz
  POST /assess   — assess one corridor (e.g. {"corridor": "NGN->ZAR"})
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel

from clients import perplexity_research
from intelligence import assess_corridor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("corridor-intelligence")

app = FastAPI(title="corridor-intelligence-service", version="0.1.0")


class AssessIn(BaseModel):
    corridor: str


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/assess")
def assess(req: AssessIn) -> dict:
    intel = assess_corridor(req.corridor, research=perplexity_research.assess)
    logger.info("corridor=%s reliability=%.2f reg_risk=%s source=%s",
                intel.corridor, intel.reliability_score, intel.regulatory_risk, intel.source)
    return asdict(intel)
