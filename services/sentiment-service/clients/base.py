"""Sentiment client base class.

Every source returns its own typed result so the aggregator can apply
per-source thresholds. Transport failures raise — the aggregator catches
and degrades to ``None`` for that source (fail-open per design).
"""

from __future__ import annotations


class SentimentClient:
    name: str

    async def fetch(self):  # pragma: no cover — overridden per source
        raise NotImplementedError
