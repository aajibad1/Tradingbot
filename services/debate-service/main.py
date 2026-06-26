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
from shared.a2a import (
    AgentCard,
    AgentProvider,
    AgentSkill,
    Message,
    base_url,
    data_part,
    install_a2a,
    text_part,
)

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
    (proposer + diverse skeptics) plugs in here; without it we fail soft to
    abstaining agents (verdict 'uncertain'), so the deterministic path is never
    blocked. DEBATE_MODEL overrides the default Claude model."""
    abstainers = (_abstain_proposer, [_abstain_skeptic for _ in range(n_skeptics)])
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return abstainers
    # Imported lazily so the service (and tests) stay importable without the SDK.
    # If the SDK is absent or the client can't be built, degrade to abstaining
    # agents rather than crashing the endpoint — same fail-soft contract as the
    # no-key path (the deterministic risk-engine is never blocked on this).
    try:
        from claude_agents import AnthropicLLMClient, DEFAULT_MODEL, build_claude_agents

        client = AnthropicLLMClient(model=os.environ.get("DEBATE_MODEL", DEFAULT_MODEL))
        return build_claude_agents(n_skeptics, client=client)
    except Exception:  # noqa: BLE001 — degrade, don't crash; ImportError, bad config, etc.
        logger.exception("Claude debate panel unavailable; falling back to abstention")
        return abstainers


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


# --- A2A (Agent2Agent) surface ------------------------------------------------
# The critic/debate agent (docs/10) exposed over A2A: peers send a claim as a
# text message; we return the calibrated verdict as a text summary + structured
# data part. Advisory only — the verdict never gates a trade.
_DEFAULT_N_SKEPTICS = 3

_A2A_CARD = AgentCard(
    name="debate-service",
    description=("Adversarial verification of an advisory trading/market claim: a "
                 "proposer argues for it, perspective-diverse skeptics try to refute "
                 "it, and a deterministic judge returns a calibrated verdict. ADVISORY."),
    url=f"{base_url('debate-service')}/a2a",
    version=app.version,
    provider=AgentProvider(organization="traditbot"),
    skills=[AgentSkill(
        id="verify-claim",
        name="Verify a claim",
        description=("Run a proposer/skeptic/judge debate over a claim and return a "
                     "support|reject|uncertain verdict with confidence + transcript."),
        tags=["verification", "advisory", "debate", "intelligence"],
        examples=["funding carry on Hyperliquid is profitable right now",
                  "the BTC/USD basis will hold through the next funding window"],
    )],
)


def _a2a_handle(message: Message):
    claim = message.text().strip()
    if not claim:
        from shared.a2a import A2AError, errors
        raise A2AError(errors.INVALID_PARAMS, "A claim (text part) is required")
    proposer, skeptics = _agents(_DEFAULT_N_SKEPTICS)
    verdict = run_debate(claim, {}, proposer, skeptics)
    summary = (f"{verdict.decision} (confidence {verdict.confidence:.2f}; "
               f"refuted {verdict.refuted_by}/{verdict.n_skeptics})")
    return [text_part(summary), data_part(asdict(verdict))]


install_a2a(app, card=_A2A_CARD, handler=_a2a_handle)
