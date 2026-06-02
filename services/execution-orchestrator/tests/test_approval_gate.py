"""Tests for the kill-switch gate and atomic approval drain (C2 + C4).

These cover the two money-path invariants the orchestrator must hold:
  * a tripped kill switch halts the live path (C2), failing SAFE on Redis error;
  * approved opportunities are claimed atomically so two concurrent ticks can
    never submit the same order twice (C4).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import approval_gate
from approval_gate import (
    KEY_KILL_SWITCH_ACTIVE,
    KEY_PREFIX_APPROVED,
    ApprovalStatus,
    PendingApproval,
    drain_approved,
    kill_switch_active,
)
from shared.models.opportunity import Opportunity, StrategyType


class _FakeRedis:
    """Minimal Redis stand-in with an atomic getdel, like Redis >= 6.2."""

    def __init__(self, raise_on_get: bool = False) -> None:
        self.kv: dict[str, str] = {}
        self.raise_on_get = raise_on_get

    def get(self, key: str) -> str | None:
        if self.raise_on_get:
            raise ConnectionError("redis down")
        return self.kv.get(key)

    def set(self, key: str, value) -> None:
        self.kv[key] = str(value)

    def setex(self, key: str, _ttl: int, value) -> None:
        self.kv[key] = str(value)

    def delete(self, key: str) -> None:
        self.kv.pop(key, None)

    def getdel(self, key: str) -> str | None:
        return self.kv.pop(key, None)

    def scan_iter(self, match: str):
        prefix = match.rstrip("*")
        # snapshot keys so deletion during iteration is safe
        return [k for k in list(self.kv) if k.startswith(prefix)]


def _opp(opp_id: str) -> Opportunity:
    return Opportunity(
        id=opp_id,
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="hyperliquid",
        gross_spread_bps=0.0,
        trading_fees_bps=31.0,
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=30.0,
        net_edge_bps=80.0,
        confidence_score=0.85,
        recommended_size_usd=10_000.0,
        min_hold_hours=6.0,
        detected_at=datetime(2026, 1, 1),
        execute=True,
    )


def _approved(redis: _FakeRedis, opp_id: str) -> None:
    now = datetime(2026, 1, 1)
    pending = PendingApproval(
        opportunity_id=opp_id,
        opportunity=_opp(opp_id),
        posted_at=now,
        expires_at=now + timedelta(minutes=10),
        status=ApprovalStatus.APPROVED,
    )
    redis.kv[f"{KEY_PREFIX_APPROVED}{opp_id}"] = pending.model_dump_json()


# ── C2: kill-switch gate ────────────────────────────────────────────────────


def test_kill_switch_active_true_when_engaged() -> None:
    r = _FakeRedis()
    r.set(KEY_KILL_SWITCH_ACTIVE, "1")
    assert kill_switch_active(r) is True


def test_kill_switch_inactive_when_unset_or_zero() -> None:
    r = _FakeRedis()
    assert kill_switch_active(r) is False
    r.set(KEY_KILL_SWITCH_ACTIVE, "0")
    assert kill_switch_active(r) is False


def test_kill_switch_fails_safe_on_redis_error() -> None:
    """If Redis is unreachable, the switch must read as ACTIVE (fail closed)."""
    r = _FakeRedis(raise_on_get=True)
    assert kill_switch_active(r) is True


# ── C4: atomic drain ────────────────────────────────────────────────────────


def test_drain_approved_returns_and_removes(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _FakeRedis()
    _approved(r, "opp-a")
    _approved(r, "opp-b")
    monkeypatch.setattr(approval_gate, "_redis", lambda: r)

    drained = drain_approved()
    assert {p.opportunity_id for p in drained} == {"opp-a", "opp-b"}
    # Keys are gone — a second drain yields nothing.
    assert drain_approved() == []


def test_drain_claims_each_key_at_most_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate two concurrent ticks: the same approved key must be handed to
    exactly one of them (getdel is atomic), never both."""
    r = _FakeRedis()
    _approved(r, "opp-shared")
    monkeypatch.setattr(approval_gate, "_redis", lambda: r)

    first = drain_approved()
    second = drain_approved()
    claimed = [p.opportunity_id for p in first + second]
    assert claimed == ["opp-shared"]  # claimed once, not twice
