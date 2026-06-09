"""Perplexity research client for corridor intelligence.

Calls Perplexity (Sonar / sonar-pro for deeper research) asking for a structured
JSON assessment of a cross-border crypto corridor's reliability, settlement
friction, and regulatory risk. Key-gated and fail-soft: no key or any error →
returns None so the caller falls back to the deterministic baseline.

Reference: https://docs.perplexity.ai/
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar-pro"   # deeper research than plain "sonar"
_PROMPT = (
    "You are a cross-border payments analyst. For the crypto/stablecoin corridor "
    "{corridor} (source->destination currency), research current conditions and "
    "return ONLY a JSON object: {{\"reliability\": <0..1, higher=more reliable "
    "settlement>, \"friction_bps\": <estimated extra cost in bps from FX drift, "
    "payout delay, rail friction>, \"regulatory_risk\": <\"low\"|\"medium\"|"
    "\"high\">, \"summary\": <<=400 chars rationale, cite recent policy/FX/rail "
    "developments>}}. No prose outside the JSON."
)


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def assess(corridor: str, timeout: float = 20.0) -> dict | None:
    """Query Perplexity for a corridor assessment, or None if unavailable."""
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return None
    try:
        import httpx

        resp = httpx.post(
            _URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": _MODEL,
                "messages": [{"role": "user", "content": _PROMPT.format(corridor=corridor)}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        parsed = _extract_json(body["choices"][0]["message"]["content"])
        # Grounding/audit: Perplexity returns the cited sources used. Carry them
        # (+ the model) so every assessment is traceable, per finance guidance.
        parsed["citations"] = body.get("citations", []) or []
        parsed["model_version"] = body.get("model", _MODEL)
        return parsed
    except Exception as e:
        logger.warning("perplexity corridor research failed for %s: %s", corridor, e)
        return None
