"""Strategy base class.

A strategy is a pure function over a `MarketSnapshot`: given the latest ticks
and funding rates, emit ScoredCandidates for the scorer to finalize. No I/O,
no state mutation — easy to unit test.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from models import MarketSnapshot
from scorer import ScoredCandidate


class Strategy(ABC):
    name: str

    @abstractmethod
    def evaluate(self, snapshot: MarketSnapshot) -> Iterable[ScoredCandidate]:
        """Return zero or more candidates from this snapshot."""
