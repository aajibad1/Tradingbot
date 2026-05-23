"""Tests for the Redis writer."""

from __future__ import annotations

import json
from datetime import datetime

from models import SentimentSignal
from writer import NAMESPACE, write_signal


class _FakeRedis:
    """Minimal in-memory stand-in: only SETEX + DELETE + GET."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def setex(self, key: str, ttl: int, value) -> None:
        self.kv[key] = str(value)
        self.ttls[key] = ttl

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            self.kv.pop(k, None)
            self.ttls.pop(k, None)
            n += 1
        return n


def _signal(**overrides) -> SentimentSignal:
    defaults = dict(
        observed_at=datetime(2026, 5, 22, 12, 0, 0),
        fear_greed_index=20,
        fear_greed_classification="Extreme Fear",
        perplexity_score=-0.5,
        perplexity_summary="bearish narrative",
        cryptopanic_panic_score=80.0,
        cryptopanic_articles_24h=120,
        is_bearish=True,
        block_reasons=["fear_greed_index 20 < 25"],
        sources_ok={"fear_greed": True, "perplexity": True, "cryptopanic": True},
    )
    defaults.update(overrides)
    return SentimentSignal(**defaults)


def test_write_signal_populates_all_keys() -> None:
    r = _FakeRedis()
    ttl = write_signal(r, _signal())
    assert ttl > 0
    assert r.kv[f"{NAMESPACE}:is_bearish"] == "1"
    assert json.loads(r.kv[f"{NAMESPACE}:block_reasons"]) == ["fear_greed_index 20 < 25"]
    assert r.kv[f"{NAMESPACE}:fear_greed_index"] == "20"
    assert r.kv[f"{NAMESPACE}:perplexity_score"] == "-0.5"
    assert r.kv[f"{NAMESPACE}:cryptopanic_panic_score"] == "80.0"
    assert r.kv[f"{NAMESPACE}:sources_ok"]


def test_write_signal_uses_zero_for_is_bearish_false() -> None:
    r = _FakeRedis()
    write_signal(r, _signal(is_bearish=False, block_reasons=[]))
    assert r.kv[f"{NAMESPACE}:is_bearish"] == "0"
    assert json.loads(r.kv[f"{NAMESPACE}:block_reasons"]) == []


def test_write_signal_deletes_missing_optional_fields() -> None:
    """When a source failed, its keys must be DELETED rather than holding stale data."""
    r = _FakeRedis()
    # Seed stale values.
    r.setex(f"{NAMESPACE}:perplexity_score", 3600, "0.9")
    r.setex(f"{NAMESPACE}:perplexity_summary", 3600, "old")

    write_signal(
        r,
        _signal(perplexity_score=None, perplexity_summary=None),
    )
    assert f"{NAMESPACE}:perplexity_score" not in r.kv
    assert f"{NAMESPACE}:perplexity_summary" not in r.kv


def test_write_signal_ttl_env_override(monkeypatch) -> None:
    monkeypatch.setenv("SENTIMENT_REDIS_TTL_S", "120")
    r = _FakeRedis()
    ttl = write_signal(r, _signal())
    assert ttl == 120
    assert r.ttls[f"{NAMESPACE}:is_bearish"] == 120


def test_write_signal_bad_ttl_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("SENTIMENT_REDIS_TTL_S", "not-a-number")
    r = _FakeRedis()
    ttl = write_signal(r, _signal())
    assert ttl > 0  # default
