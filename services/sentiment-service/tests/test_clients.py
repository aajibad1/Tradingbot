"""Tests for the three source clients.

We don't talk to the live APIs in tests — each client takes a
``transport`` constructor parameter so we can inject ``httpx.MockTransport``
in tests. Production code never passes that arg.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from clients.cryptopanic import (
    CryptoPanicClient,
    _compute_panic_score,
)
from clients.fear_greed import FearGreedClient
from clients.perplexity import (
    PerplexityClient,
    _extract_json_blob,
)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Fear & Greed                                                                #
# --------------------------------------------------------------------------- #


def test_fear_greed_parses_latest_row() -> None:
    payload = {
        "data": [
            {"value": "37", "value_classification": "Fear", "timestamp": "1700000000"}
        ]
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))

    client = FearGreedClient(transport=transport)
    result = _run(client.fetch())
    assert result.index == 37
    assert result.classification == "Fear"


def test_fear_greed_empty_data_raises() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": []}))
    client = FearGreedClient(transport=transport)
    with pytest.raises(RuntimeError, match="no data rows"):
        _run(client.fetch())


# --------------------------------------------------------------------------- #
# Perplexity                                                                  #
# --------------------------------------------------------------------------- #


def test_perplexity_extracts_json_from_raw_string() -> None:
    assert _extract_json_blob('{"score": 0.5, "summary": "ok"}') == {
        "score": 0.5,
        "summary": "ok",
    }


def test_perplexity_extracts_json_with_prose_wrapper() -> None:
    raw = 'Here is the JSON: {"score": -0.3, "summary": "bearish"} — done.'
    assert _extract_json_blob(raw) == {"score": -0.3, "summary": "bearish"}


def test_perplexity_extract_raises_when_no_json() -> None:
    with pytest.raises(RuntimeError, match="no JSON object"):
        _extract_json_blob("no json here")


def test_perplexity_fetch_clamps_score(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_SECRET_PERPLEXITY_API_KEY", "test-key")
    payload = {
        "choices": [
            {"message": {"content": json.dumps({"score": 2.5, "summary": "x"})}}
        ]
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))

    client = PerplexityClient(transport=transport)
    result = _run(client.fetch())
    assert result.score == 1.0  # clamped from 2.5
    assert result.summary == "x"


def test_perplexity_missing_api_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("LOCAL_SECRET_PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    # Clear the lru_cache so the test doesn't see a cached "available" result
    # from a previous test that set the env var.
    from secret_loader import get_secret

    get_secret.cache_clear()
    with pytest.raises(RuntimeError, match="PERPLEXITY_API_KEY unset"):
        PerplexityClient()


# --------------------------------------------------------------------------- #
# CryptoPanic                                                                 #
# --------------------------------------------------------------------------- #


def test_panic_score_zero_when_no_posts() -> None:
    assert _compute_panic_score([]) == 0.0


def test_panic_score_zero_when_no_votes() -> None:
    posts = [{"votes": {}} for _ in range(5)]
    assert _compute_panic_score(posts) == 0.0


def test_panic_score_pure_bearish_at_low_volume() -> None:
    """100% bearish votes but only 5 articles → score around 50 (volume-damped)."""
    posts = [{"votes": {"negative": 10}} for _ in range(5)]
    score = _compute_panic_score(posts)
    # panic_share = 1.0, volume_factor = 5/50 = 0.1, multiplier = 0.55 → 55.0
    assert 50.0 <= score <= 60.0


def test_panic_score_pure_bearish_at_high_volume() -> None:
    """100% bearish votes AND high volume → score near 100."""
    posts = [{"votes": {"negative": 10}} for _ in range(100)]
    score = _compute_panic_score(posts)
    # panic_share = 1.0, volume_factor = 1.0, multiplier = 1.0 → 100.0
    assert score == 100.0


def test_panic_score_balanced_is_low() -> None:
    posts = [{"votes": {"negative": 5, "positive": 5}} for _ in range(50)]
    score = _compute_panic_score(posts)
    # panic_share = 0.5, volume_factor = 1.0, multiplier = 1.0 → 50.0
    assert score == 50.0


def test_cryptopanic_missing_api_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("CRYPTOPANIC_API_KEY", raising=False)
    monkeypatch.delenv("LOCAL_SECRET_CRYPTOPANIC_API_KEY", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    from secret_loader import get_secret

    get_secret.cache_clear()
    with pytest.raises(RuntimeError, match="CRYPTOPANIC_API_KEY unset"):
        CryptoPanicClient()


def test_cryptopanic_fetch(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_SECRET_CRYPTOPANIC_API_KEY", "test-token")
    payload = {
        "results": [
            {"votes": {"negative": 5, "toxic": 2, "positive": 1, "important": 0}}
            for _ in range(30)
        ]
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))

    client = CryptoPanicClient(transport=transport)
    result = _run(client.fetch())
    assert result.articles_24h == 30
    assert 60.0 <= result.panic_score <= 100.0
