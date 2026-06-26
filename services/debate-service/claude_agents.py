"""Claude-backed debate personas (proposer + diverse skeptics) — ADVISORY.

`debate.py` orchestrates injected callables, so this module only has to (a) build
persona callables that consult an LLM and (b) map the LLM's structured reply onto a
``Position``. The LLM client is *itself* injected (``LLMClient`` protocol), so the
personas are fully testable without network access or an API key: a fake client
returning canned JSON exercises the exact parse → clamp → map path that production
runs against Claude. Without ``ANTHROPIC_API_KEY`` the service never reaches this
module (main.py falls back to abstaining agents), so the live path is key-gated and
fail-soft, matching every other service's degrade-without-creds behaviour.

The personas never gate a trade — the deterministic risk-engine remains the only
authority. The full transcript + model version come back so every verdict is auditable.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Protocol

from debate import Position

logger = logging.getLogger("debate-service.agents")

# Default model — the canonical, most-capable Claude (see CLAUDE_CODE_PROMPT/AI Ops).
DEFAULT_MODEL = "claude-opus-4-8"

# Each skeptic attacks from a distinct angle — perspective-diverse refutation catches
# failure modes that N identical skeptics would all miss the same way.
_SKEPTIC_LENSES: list[tuple[str, str]] = [
    ("data", "Attack the claim on DATA QUALITY: is the underlying data reliable, "
             "recent, and sufficient, or is the edge an artifact of thin/stale data?"),
    ("execution", "Attack the claim on EXECUTION RISK: slippage, taker fees, latency, "
                  "partial fills, and whether the modeled edge survives real execution."),
    ("adversarial", "Attack the claim ADVERSARIALLY: who takes the other side of this "
                    "trade and why might they be right? What does the consensus miss?"),
    ("regime", "Attack the claim on REGIME/TIMING: does it hold across volatility "
               "regimes, or only in the one we happen to be in right now?"),
    ("liquidity", "Attack the claim on LIQUIDITY and capacity: can meaningful size be "
                  "deployed without moving the market against the position?"),
]

_PROPOSER_SYSTEM = (
    "You are the PROPOSER in an adversarial debate that vets an advisory trading/market "
    "claim. Argue FOR the claim as strongly as the evidence honestly allows — but do not "
    "overstate: your confidence must track how well-supported the claim actually is. You "
    "are advisory only; a deterministic risk engine makes the final decision, never you."
)

_SKEPTIC_SYSTEM = (
    "You are a SKEPTIC in an adversarial debate that vets an advisory trading/market "
    "claim. Your job is to REFUTE the claim when it is weak. {lens} Be a genuine skeptic: "
    "choose 'refute' when you find a real flaw; choose 'uncertain' only when you truly "
    "cannot refute it. You are advisory only and never make the final decision."
)

# Structured-output schemas. Numeric range constraints aren't enforceable in the schema,
# so confidence is clamped to [0,1] client-side after parsing.
_PROPOSER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["support"]},
        "confidence": {"type": "number"},
        "argument": {"type": "string"},
    },
    "required": ["stance", "confidence", "argument"],
    "additionalProperties": False,
}

_SKEPTIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["refute", "uncertain"]},
        "confidence": {"type": "number"},
        "argument": {"type": "string"},
    },
    "required": ["stance", "confidence", "argument"],
    "additionalProperties": False,
}


class LLMClient(Protocol):
    """Minimal seam over the Anthropic Messages API. Injected so personas are testable
    without the SDK: ``model`` labels the transcript, ``complete`` returns parsed JSON."""

    model: str

    def complete(self, *, system: str, user: str, schema: dict) -> dict: ...


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _to_position(role: str, data: dict, allowed: list[str], model: str | None) -> Position:
    """Map a parsed LLM reply onto a Position, defending against malformed fields."""
    stance = data.get("stance")
    if stance not in allowed:
        stance = allowed[0]  # schema should prevent this; stay safe if it slips through
    try:
        conf = _clamp(float(data.get("confidence", 0.5)))
    except (TypeError, ValueError):
        conf = 0.5
    argument = str(data.get("argument") or "").strip() or "(no argument given)"
    return Position(role=role, stance=stance, confidence=conf,
                    argument=argument, model_version=model)


def _abstain(role: str, stance: str, reason: str) -> Position:
    """Fail-soft fallback — one LLM error must not crash the whole debate."""
    return Position(role=role, stance=stance, confidence=0.5,
                    argument=f"(abstaining — {reason})", model_version=None)


def _proposer(client: LLMClient) -> Callable[[str, dict], Position]:
    def proposer(claim: str, context: dict) -> Position:
        user = f"Claim: {claim}\nContext: {json.dumps(context, default=str)}"
        try:
            data = client.complete(system=_PROPOSER_SYSTEM, user=user, schema=_PROPOSER_SCHEMA)
        except Exception:  # noqa: BLE001 — degrade, don't crash the panel
            logger.exception("proposer LLM call failed")
            return _abstain("proposer", "support", "proposer call failed")
        return _to_position("proposer", data, ["support"], client.model)
    return proposer


def _skeptic(client: LLMClient, lens: str) -> Callable[[str, dict, Position], Position]:
    system = _SKEPTIC_SYSTEM.format(lens=lens)

    def skeptic(claim: str, context: dict, proposer: Position) -> Position:
        user = (
            f"Claim: {claim}\n"
            f"Context: {json.dumps(context, default=str)}\n"
            f"Proposer's case (stance={proposer.stance}, "
            f"confidence={proposer.confidence:.2f}): {proposer.argument}"
        )
        try:
            data = client.complete(system=system, user=user, schema=_SKEPTIC_SCHEMA)
        except Exception:  # noqa: BLE001 — degrade, don't crash the panel
            logger.exception("skeptic LLM call failed")
            return _abstain("skeptic", "uncertain", "skeptic call failed")
        return _to_position("skeptic", data, ["refute", "uncertain"], client.model)
    return skeptic


def build_claude_agents(
    n_skeptics: int, client: LLMClient
) -> tuple[Callable[[str, dict], Position], list[Callable[[str, dict, Position], Position]]]:
    """Build one proposer + ``n_skeptics`` skeptics, each with a distinct refutation lens,
    all backed by ``client``. Drop-in for debate.run_debate's injected callables."""
    proposer = _proposer(client)
    skeptics = [
        _skeptic(client, _SKEPTIC_LENSES[i % len(_SKEPTIC_LENSES)][1])
        for i in range(n_skeptics)
    ]
    return proposer, skeptics


class AnthropicLLMClient:
    """Production ``LLMClient`` over the Anthropic Messages API with structured outputs.

    The ``anthropic`` SDK is imported lazily so this module stays importable (and the
    personas stay unit-testable) without the dependency installed. Constructed only when
    ANTHROPIC_API_KEY is set, so the key is resolved from the environment by the SDK."""

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 2000) -> None:
        import anthropic  # lazy: keeps the module import-safe without the SDK

        self.model = model
        self._max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    def complete(self, *, system: str, user: str, schema: dict) -> dict:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text)
