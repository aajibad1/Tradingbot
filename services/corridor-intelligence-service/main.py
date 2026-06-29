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
import os
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel

from clients import perplexity_research
from intelligence import CorridorIntel, assess_corridor
from shared.a2a import A2AClient, base_url, client_for, find_agents_with_skill

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("corridor-intelligence")

app = FastAPI(title="corridor-intelligence-service", version="0.1.0")


class AssessIn(BaseModel):
    corridor: str


def _resolve_debate_base() -> str | None:
    """Resolve the debate peer's A2A base URL, or None to skip verification.

    Opt-in by design — verification is purely additive, so /assess stays silent
    unless asked. An explicit ``A2A_DEBATE_SERVICE_URL`` wins (pinning / back-compat);
    otherwise, only when discovery is explicitly enabled (``A2A_DISCOVER_DEBATE``
    truthy) do we route by capability to whoever advertises the ``verify-claim``
    skill. Absent both, return None. Discovery is time-bounded so a dead peer can't
    slow /assess."""
    explicit = os.environ.get("A2A_DEBATE_SERVICE_URL")
    if explicit:
        return explicit
    if os.environ.get("A2A_DISCOVER_DEBATE", "").strip().lower() in ("1", "true", "yes", "on"):
        providers = find_agents_with_skill(
            "verify-claim", client_factory=lambda n: client_for(n, timeout=2.0)
        )
        if providers:
            return base_url(providers[0])
    return None


def _verify_reliability(intel: CorridorIntel) -> dict | None:
    """Optionally adversarially-verify the reliability claim via the debate-service
    over A2A (debate.py names 'corridor reliability' as a use case). Opt-in and
    fail-soft: the peer is resolved by ``_resolve_debate_base`` (explicit env, else
    capability discovery when enabled); any error returns None — verification is
    purely additive and never blocks or alters the assessment. Advisory on advisory;
    the deterministic scorer still decides."""
    base = _resolve_debate_base()
    if not base:
        return None
    claim = (f"Corridor {intel.corridor} is reliable for settlement "
             f"(reliability {intel.reliability_score:.2f}, "
             f"regulatory risk {intel.regulatory_risk}).")
    try:
        task = A2AClient(base, timeout=10.0).send_text(claim)
        verdict = (task.status.message.data() if task.status.message else None) or {}
    except Exception:  # noqa: BLE001 — advisory verification must never break /assess
        logger.warning("debate verification unavailable; returning assessment as-is")
        return None
    if not verdict.get("decision"):
        return None
    return {"decision": verdict["decision"], "confidence": verdict.get("confidence"),
            "refuted_by": verdict.get("refuted_by"), "n_skeptics": verdict.get("n_skeptics")}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/assess")
def assess(req: AssessIn) -> dict:
    intel = assess_corridor(req.corridor, research=perplexity_research.assess)
    logger.info("corridor=%s reliability=%.2f reg_risk=%s source=%s",
                intel.corridor, intel.reliability_score, intel.regulatory_risk, intel.source)
    out = asdict(intel)
    verdict = _verify_reliability(intel)
    if verdict is not None:
        out["debate"] = verdict  # additive; present only when A2A debate is configured
    return out
