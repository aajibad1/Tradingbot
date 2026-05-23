"""Tests for the sentiment aggregator + threshold rule.

Each test exercises the combining function with different combinations of
source results. The aggregator uses async clients; here we substitute
``_StubClient`` objects that return canned results.
"""

from __future__ import annotations

import asyncio

import pytest

from aggregator import aggregate, _evaluate_block
from clients.cryptopanic import CryptoPanicResult
from clients.fear_greed import FearGreedResult
from clients.perplexity import PerplexityResult


class _StubClient:
    def __init__(self, name: str, result=None, raises: Exception | None = None) -> None:
        self.name = name
        self._result = result
        self._raises = raises

    async def fetch(self):
        if self._raises is not None:
            raise self._raises
        return self._result


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Threshold rule (pure)                                                       #
# --------------------------------------------------------------------------- #


def test_evaluate_block_no_signals_is_not_bearish() -> None:
    is_bearish, reasons = _evaluate_block(None, None, None)
    assert is_bearish is False
    assert reasons == []


def test_evaluate_block_low_fg_trips() -> None:
    is_bearish, reasons = _evaluate_block(
        FearGreedResult(index=10, classification="Extreme Fear"), None, None
    )
    assert is_bearish is True
    assert any("fear_greed_index" in r for r in reasons)


def test_evaluate_block_negative_perplexity_trips() -> None:
    is_bearish, reasons = _evaluate_block(
        None, PerplexityResult(score=-0.5, summary="bad"), None
    )
    assert is_bearish is True
    assert any("perplexity_score" in r for r in reasons)


def test_evaluate_block_high_cryptopanic_trips() -> None:
    is_bearish, reasons = _evaluate_block(
        None, None, CryptoPanicResult(panic_score=85.0, articles_24h=120)
    )
    assert is_bearish is True
    assert any("cryptopanic" in r for r in reasons)


def test_evaluate_block_all_neutral_passes() -> None:
    is_bearish, reasons = _evaluate_block(
        FearGreedResult(index=55, classification="Greed"),
        PerplexityResult(score=0.2, summary="ok"),
        CryptoPanicResult(panic_score=30.0, articles_24h=40),
    )
    assert is_bearish is False
    assert reasons == []


def test_evaluate_block_thresholds_respect_env(monkeypatch) -> None:
    # Tighten F&G threshold so 50 trips it.
    monkeypatch.setenv("SENTIMENT_FG_BLOCK_THRESHOLD", "55")
    is_bearish, reasons = _evaluate_block(
        FearGreedResult(index=50, classification="Neutral"), None, None
    )
    assert is_bearish is True
    assert any("fear_greed_index 50" in r for r in reasons)


# --------------------------------------------------------------------------- #
# aggregate() — async fanout + fail-open                                      #
# --------------------------------------------------------------------------- #


def test_aggregate_with_all_sources_bullish_returns_not_bearish() -> None:
    fg = _StubClient("fear_greed", FearGreedResult(index=60, classification="Greed"))
    px = _StubClient("perplexity", PerplexityResult(score=0.3, summary="green"))
    cp = _StubClient("cryptopanic", CryptoPanicResult(panic_score=20.0, articles_24h=10))
    signal = _run(aggregate(fg, px, cp))
    assert signal.is_bearish is False
    assert signal.block_reasons == []
    assert signal.sources_ok == {"fear_greed": True, "perplexity": True, "cryptopanic": True}
    assert signal.fear_greed_index == 60
    assert signal.perplexity_score == pytest.approx(0.3)
    assert signal.cryptopanic_panic_score == pytest.approx(20.0)


def test_aggregate_one_source_bearish_blocks() -> None:
    fg = _StubClient("fear_greed", FearGreedResult(index=15, classification="Extreme Fear"))
    px = _StubClient("perplexity", PerplexityResult(score=0.2, summary="green"))
    cp = _StubClient("cryptopanic", CryptoPanicResult(panic_score=20.0, articles_24h=10))
    signal = _run(aggregate(fg, px, cp))
    assert signal.is_bearish is True
    assert len(signal.block_reasons) == 1
    assert "fear_greed_index" in signal.block_reasons[0]


def test_aggregate_failed_source_does_not_block() -> None:
    """Fail-open: a transport failure must NOT contribute a block reason."""
    fg = _StubClient("fear_greed", raises=RuntimeError("boom"))
    px = _StubClient("perplexity", PerplexityResult(score=0.1, summary="ok"))
    cp = _StubClient("cryptopanic", CryptoPanicResult(panic_score=30.0, articles_24h=20))
    signal = _run(aggregate(fg, px, cp))
    assert signal.is_bearish is False
    assert signal.sources_ok == {"fear_greed": False, "perplexity": True, "cryptopanic": True}
    assert signal.fear_greed_index is None


def test_aggregate_all_sources_failed_is_not_bearish() -> None:
    """All-failed is the canonical fail-open case: no block, no bearish."""
    fg = _StubClient("fear_greed", raises=RuntimeError("a"))
    px = _StubClient("perplexity", raises=RuntimeError("b"))
    cp = _StubClient("cryptopanic", raises=RuntimeError("c"))
    signal = _run(aggregate(fg, px, cp))
    assert signal.is_bearish is False
    assert signal.block_reasons == []
    assert signal.sources_ok == {"fear_greed": False, "perplexity": False, "cryptopanic": False}


def test_aggregate_only_one_source_configured() -> None:
    fg = _StubClient("fear_greed", FearGreedResult(index=12, classification="Extreme Fear"))
    signal = _run(aggregate(fg, None, None))
    assert signal.is_bearish is True
    assert signal.sources_ok == {"fear_greed": True, "perplexity": False, "cryptopanic": False}
    assert signal.perplexity_score is None
    assert signal.cryptopanic_panic_score is None
