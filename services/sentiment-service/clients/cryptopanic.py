"""CryptoPanic news + panic-score client.

Reference: https://cryptopanic.com/developers/api/

CryptoPanic exposes a vote-aggregated news feed. Each post carries
``votes`` with fields like ``negative``, ``positive``, ``important``,
``saved``, ``toxic``. We pull the last 24h of posts and derive a
0..100 "panic score" from the ratio of bearish-leaning votes to total
votes, scaled by post volume so a single trending panic post can't
dominate.

Formula::

    panic_share = sum(votes.negative + votes.toxic) /
                  max(1, sum(votes.negative + votes.toxic +
                             votes.positive + votes.important))
    volume_factor = min(1.0, articles_24h / VOLUME_NORMALIZER)
    panic_score = 100 * panic_share * (0.5 + 0.5 * volume_factor)

The ``0.5 + 0.5 * volume_factor`` keeps a low-volume but high-share day
from immediately tripping the gate while still letting genuine panic days
(both share and volume elevated) score high.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from clients.base import SentimentClient
from secret_loader import get_secret

logger = logging.getLogger(__name__)


_CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
_VOLUME_NORMALIZER = 50  # 50 articles in 24h = "high volume" anchor


@dataclass(frozen=True)
class CryptoPanicResult:
    panic_score: float  # 0..100
    articles_24h: int


def _compute_panic_score(posts: list[dict]) -> float:
    bearish = 0
    bullish = 0
    for post in posts:
        votes = post.get("votes") or {}
        try:
            bearish += int(votes.get("negative", 0)) + int(votes.get("toxic", 0))
            bullish += int(votes.get("positive", 0)) + int(votes.get("important", 0))
        except (TypeError, ValueError):
            continue
    total = bearish + bullish
    if total <= 0:
        return 0.0
    panic_share = bearish / total
    volume_factor = min(1.0, len(posts) / _VOLUME_NORMALIZER)
    score = 100.0 * panic_share * (0.5 + 0.5 * volume_factor)
    return max(0.0, min(100.0, score))


class CryptoPanicClient(SentimentClient):
    name = "cryptopanic"

    def __init__(
        self,
        api_key: str | None = None,
        url: str = _CRYPTOPANIC_URL,
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("CRYPTOPANIC_API_KEY")
            or get_secret("CRYPTOPANIC_API_KEY")
        )
        if not self.api_key:
            raise RuntimeError(
                "CRYPTOPANIC_API_KEY unset — set CRYPTOPANIC_API_KEY env var, "
                "LOCAL_SECRET_CRYPTOPANIC_API_KEY, or configure in Secret Manager"
            )
        self.url = url
        self.timeout_s = timeout_s
        self._transport = transport

    async def fetch(self) -> CryptoPanicResult:
        params = {
            "auth_token": self.api_key,
            "filter": "hot",
            "kind": "news",
            "public": "true",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_s, transport=self._transport
        ) as client:
            response = await client.get(self.url, params=params)
            response.raise_for_status()
            payload = response.json()

        posts = payload.get("results") or []
        score = _compute_panic_score(posts)
        return CryptoPanicResult(panic_score=score, articles_24h=len(posts))
