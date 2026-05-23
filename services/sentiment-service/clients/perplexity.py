"""Perplexity Sonar sentiment client.

Reference: https://docs.perplexity.ai/

Calls the chat-completions endpoint with the ``sonar`` model and a tight
prompt that asks for a JSON object {score, summary}. We parse that out
and clamp ``score`` into [-1.0, +1.0].

The prompt deliberately uses a 4-hour news horizon (matches our refresh
cadence) and asks for *crypto market sentiment*, not single-asset
sentiment, since the gate is portfolio-wide.

Calls cost a few cents each; with the 4h refresh this is < $5/month.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

import httpx

from clients.base import SentimentClient
from secret_loader import get_secret

logger = logging.getLogger(__name__)


_PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar"
_PROMPT = (
    "You are a sentiment analyst tracking the broad crypto market over the "
    "past 4 hours. Return a JSON object with two fields and nothing else: "
    "{\"score\": <float in -1.0 to +1.0, negative=bearish, positive=bullish>, "
    "\"summary\": <one-paragraph rationale, <= 600 chars>}. Do not include "
    "any prose outside the JSON object. Anchor on price action, funding "
    "rates, ETF flows, and macro news. If the last 4h is uneventful, lean "
    "toward 0."
)


@dataclass(frozen=True)
class PerplexityResult:
    score: float  # clamped to [-1.0, +1.0]
    summary: str


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _extract_json_blob(text: str) -> dict:
    """Pull the first {...} JSON object out of a model response."""
    # Try a direct parse first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to a balanced-brace scan.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"no JSON object in perplexity response: {text!r}")
    return json.loads(match.group(0))


class PerplexityClient(SentimentClient):
    name = "perplexity"

    def __init__(
        self,
        api_key: str | None = None,
        url: str = _PERPLEXITY_URL,
        model: str = _MODEL,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("PERPLEXITY_API_KEY")
            or get_secret("PERPLEXITY_API_KEY")
        )
        if not self.api_key:
            raise RuntimeError(
                "PERPLEXITY_API_KEY unset — set PERPLEXITY_API_KEY env var, "
                "LOCAL_SECRET_PERPLEXITY_API_KEY, or configure in Secret Manager"
            )
        self.url = url
        self.model = model
        self.timeout_s = timeout_s
        self._transport = transport

    async def fetch(self) -> PerplexityResult:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": "What is the current crypto market sentiment?"},
            ],
            "temperature": 0.0,
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_s, transport=self._transport
        ) as client:
            response = await client.post(self.url, json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"perplexity response missing choices: {payload!r}") from e

        blob = _extract_json_blob(content)
        try:
            score = _clamp(float(blob["score"]))
        except (KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"perplexity JSON missing score: {blob!r}") from e
        summary = str(blob.get("summary", ""))[:600]
        return PerplexityResult(score=score, summary=summary)
