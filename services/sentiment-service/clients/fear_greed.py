"""Alternative.me Fear & Greed Index client.

Reference: https://alternative.me/crypto/fear-and-greed-index/

Free, unauthenticated, JSON over HTTPS. The "?limit=1" form returns just
the latest day's value and classification. We never need history here —
the aggregator only cares about the most recent reading.

Schema (truncated):

    {
      "data": [
        {
          "value": "42",
          "value_classification": "Fear",
          "timestamp": "1731715200",
          "time_until_update": "..."
        }
      ]
    }
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from clients.base import SentimentClient


_FNG_URL = "https://api.alternative.me/fng/?limit=1&format=json"


@dataclass(frozen=True)
class FearGreedResult:
    index: int  # 0..100
    classification: str  # 'Extreme Fear', 'Fear', 'Neutral', 'Greed', 'Extreme Greed'


class FearGreedClient(SentimentClient):
    name = "fear_greed"

    def __init__(
        self,
        url: str = _FNG_URL,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.timeout_s = timeout_s
        # ``transport`` is None in production; tests inject httpx.MockTransport.
        self._transport = transport

    async def fetch(self) -> FearGreedResult:
        async with httpx.AsyncClient(
            timeout=self.timeout_s, transport=self._transport
        ) as client:
            response = await client.get(self.url)
            response.raise_for_status()
            payload = response.json()

        rows = payload.get("data") or []
        if not rows:
            raise RuntimeError("F&G payload contained no data rows")
        row = rows[0]
        try:
            index = int(row["value"])
        except (KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"F&G payload missing/bad value: {row!r}") from e
        classification = str(row.get("value_classification", ""))
        return FearGreedResult(index=index, classification=classification)
