"""Sentiment source clients.

Each client implements ``fetch()`` and returns its own typed result. The
``aggregator`` module merges all three into a ``SentimentSignal``.
"""

from clients.base import SentimentClient
from clients.cryptopanic import CryptoPanicClient, CryptoPanicResult
from clients.fear_greed import FearGreedClient, FearGreedResult
from clients.perplexity import PerplexityClient, PerplexityResult

__all__ = [
    "CryptoPanicClient",
    "CryptoPanicResult",
    "FearGreedClient",
    "FearGreedResult",
    "PerplexityClient",
    "PerplexityResult",
    "SentimentClient",
]
