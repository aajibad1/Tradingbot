"""Tested failover for the advisory opportunity-ranker dependency.

Reliability is the #1 product priority (docs/MASTER_STRATEGY.md) and the
architecture's headline safety claim is: *"if ranking fails, deterministic
filtering still works"* (docs/10-agent-governance.md: AI is advisory, no
unrestricted autonomous execution; fail-safe). The ranker is ADVISORY only —
the deterministic risk gate is the sole authority.

These tests prove that contract in code, not just prose:

  1. ``_advisory_rank`` returns ``None`` (never raises) for every failure mode:
     no URL configured, connection refused, timeout, and a 5xx response.
  2. On success it returns the parsed JSON annotation.
  3. End to end: ``_on_opportunity`` still forwards the APPROVED opportunity
     (``execute=True``) and still emits the RiskDecision fact even when the
     ranker is configured but completely down — the advisory failure is
     invisible to the choke point.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from shared.models.opportunity import Opportunity, StrategyType
from shared.pubsub.publisher import Topic


def _opp(net_edge_bps: float = 75.0, size_usd: float = 1_000.0) -> Opportunity:
    return Opportunity(
        id="o-failopen",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="hyperliquid",
        gross_spread_bps=120.0,
        trading_fees_bps=31.0,
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=12.0,
        net_edge_bps=net_edge_bps,
        confidence_score=0.85,
        recommended_size_usd=size_usd,
        min_hold_hours=6.0,
        detected_at=datetime(2026, 1, 1),
    )


def _seed_health(fake_redis, latency_ms: float = 50.0) -> None:
    fake_redis.set("health:latency:kraken", str(latency_ms))
    fake_redis.set("health:latency:hyperliquid", str(latency_ms))


class _CapturePublisher:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, attributes=None):
        self.published.append((topic, payload, attributes))
        return "msg-id"


class _FakeMessage:
    def __init__(self, payload: Opportunity):
        self.data = payload.model_dump_json().encode()
        self.acked = False
        self.nacked = False

    def ack(self):
        self.acked = True

    def nack(self):
        self.nacked = True


# --- _advisory_rank unit behaviour ------------------------------------------ #


def test_advisory_rank_none_when_unconfigured(fake_redis, monkeypatch):
    """No ranker URL → advisory is skipped, returns None (never raises)."""
    import main

    monkeypatch.delenv("OPPORTUNITY_RANKER_URL", raising=False)
    assert main._advisory_rank(_opp()) is None


def test_advisory_rank_failopen_on_connection_error(fake_redis, monkeypatch):
    """Ranker unreachable (connection refused) → None, not an exception."""
    import main

    monkeypatch.setenv("OPPORTUNITY_RANKER_URL", "http://ranker.invalid:9")

    def _boom(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _boom)
    assert main._advisory_rank(_opp()) is None


def test_advisory_rank_failopen_on_timeout(fake_redis, monkeypatch):
    """Ranker too slow → the 1s timeout surfaces as None, never blocks."""
    import main

    monkeypatch.setenv("OPPORTUNITY_RANKER_URL", "http://ranker.local")

    def _slow(*_a, **_k):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "post", _slow)
    assert main._advisory_rank(_opp()) is None


def test_advisory_rank_failopen_on_5xx(fake_redis, monkeypatch):
    """Ranker returns 500 → raise_for_status trips → None (fail-open)."""
    import main

    monkeypatch.setenv("OPPORTUNITY_RANKER_URL", "http://ranker.local")

    def _err(*_a, **_k):
        return httpx.Response(503, request=httpx.Request("POST", "http://ranker.local/score"))

    monkeypatch.setattr(httpx, "post", _err)
    assert main._advisory_rank(_opp()) is None


def test_advisory_rank_returns_annotation_on_success(fake_redis, monkeypatch):
    """Healthy ranker → parsed JSON annotation is returned verbatim."""
    import main

    monkeypatch.setenv("OPPORTUNITY_RANKER_URL", "http://ranker.local")
    annotation = {
        "recommended_action": "execute",
        "profitability_probability": 0.72,
        "model_version": "ranker-v1",
    }

    def _ok(*_a, **_k):
        return httpx.Response(
            200,
            json=annotation,
            request=httpx.Request("POST", "http://ranker.local/score"),
        )

    monkeypatch.setattr(httpx, "post", _ok)
    assert main._advisory_rank(_opp()) == annotation


# --- end-to-end: choke point survives a dead ranker ------------------------- #


def test_on_opportunity_forwards_approved_when_ranker_down(fake_redis, monkeypatch):
    """The headline claim: a configured-but-down ranker NEVER blocks approval.

    The deterministic gate approves, the opportunity is forwarded with
    execute=True, and the RiskDecision fact is still emitted — the advisory
    failure is invisible downstream (no rank_* attributes attached)."""
    _seed_health(fake_redis)
    import main

    pub = _CapturePublisher()
    monkeypatch.setattr(main, "_get_publisher", lambda: pub)
    monkeypatch.setenv("OPPORTUNITY_RANKER_URL", "http://ranker.invalid:9")
    monkeypatch.setattr(
        httpx, "post", lambda *_a, **_k: (_ for _ in ()).throw(httpx.ConnectError("down"))
    )

    msg = _FakeMessage(_opp())
    main._on_opportunity(msg)

    assert msg.acked and not msg.nacked

    approved = [p for p in pub.published if p[0] == Topic.APPROVED_OPPORTUNITIES]
    assert len(approved) == 1
    _, payload, attrs = approved[0]
    assert payload.execute is True
    assert payload.id == "o-failopen"
    # Advisory failed open: no rank annotation leaked onto the message.
    assert "rank_action" not in attrs
    assert "rank_prob" not in attrs

    # Instrumentation still fires, with no advisory ranking attached.
    decisions = [p for p in pub.published if p[0] == Topic.RISK_DECISIONS]
    assert len(decisions) == 1
    assert decisions[0][1].approved is True
    assert decisions[0][1].advisory_action is None


def test_on_opportunity_attaches_rank_when_ranker_healthy(fake_redis, monkeypatch):
    """Counterpart: a healthy ranker DOES annotate the forwarded opportunity,
    so the fail-open path above is proven distinct from the happy path."""
    _seed_health(fake_redis)
    import main

    pub = _CapturePublisher()
    monkeypatch.setattr(main, "_get_publisher", lambda: pub)
    monkeypatch.setenv("OPPORTUNITY_RANKER_URL", "http://ranker.local")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_k: httpx.Response(
            200,
            json={"recommended_action": "execute", "profitability_probability": 0.72},
            request=httpx.Request("POST", "http://ranker.local/score"),
        ),
    )

    msg = _FakeMessage(_opp())
    main._on_opportunity(msg)

    approved = [p for p in pub.published if p[0] == Topic.APPROVED_OPPORTUNITIES]
    assert len(approved) == 1
    _, _payload, attrs = approved[0]
    assert attrs["rank_action"] == "execute"
    assert attrs["rank_prob"] == "0.72"
