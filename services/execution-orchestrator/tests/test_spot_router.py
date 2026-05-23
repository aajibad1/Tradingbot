"""Tests for the spot leg failover router."""

from __future__ import annotations

from datetime import datetime

import spot_router
from spot_router import (
    FALLBACK_SPOT_EXCHANGE,
    PRIMARY_SPOT_EXCHANGE,
    route_for_execution,
)

from shared.models.opportunity import Opportunity, StrategyType


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def set(self, key: str, value) -> None:
        self.kv[key] = str(value)


def _opp(long_exchange: str = PRIMARY_SPOT_EXCHANGE) -> Opportunity:
    return Opportunity(
        id="opp-r1",
        strategy=StrategyType.FUNDING_RATE_ARB,
        asset="BTC",
        long_exchange=long_exchange,
        short_exchange="hyperliquid",
        gross_spread_bps=120.0,
        trading_fees_bps=31.0,
        slippage_estimate_bps=4.0,
        funding_rate_annualized_pct=12.0,
        net_edge_bps=80.0,
        confidence_score=0.85,
        recommended_size_usd=10_000.0,
        min_hold_hours=6.0,
        detected_at=datetime(2026, 1, 1),
        execute=True,
    )


def test_no_failover_when_primary_healthy() -> None:
    r = _FakeRedis()
    r.set(f"health:latency:{PRIMARY_SPOT_EXCHANGE}", "50")
    r.set(f"health:latency:{FALLBACK_SPOT_EXCHANGE}", "60")
    decision = route_for_execution(r, _opp())
    assert decision.failed_over is False
    assert decision.opportunity.long_exchange == PRIMARY_SPOT_EXCHANGE


def test_failover_when_primary_unhealthy_and_fallback_healthy() -> None:
    r = _FakeRedis()
    r.set(f"health:latency:{PRIMARY_SPOT_EXCHANGE}", "900")  # very slow
    r.set(f"health:latency:{FALLBACK_SPOT_EXCHANGE}", "60")
    decision = route_for_execution(r, _opp())
    assert decision.failed_over is True
    assert decision.opportunity.long_exchange == FALLBACK_SPOT_EXCHANGE
    assert decision.reason is not None and FALLBACK_SPOT_EXCHANGE in decision.reason


def test_no_failover_when_both_unhealthy() -> None:
    r = _FakeRedis()
    r.set(f"health:latency:{PRIMARY_SPOT_EXCHANGE}", "900")
    r.set(f"health:latency:{FALLBACK_SPOT_EXCHANGE}", "800")
    decision = route_for_execution(r, _opp())
    assert decision.failed_over is False
    assert decision.opportunity.long_exchange == PRIMARY_SPOT_EXCHANGE
    assert decision.reason == "fallback also unhealthy"


def test_no_failover_when_long_leg_is_not_primary_spot() -> None:
    """The router only touches the long leg if it's on the configured
    primary spot venue. Opportunities long-on-kraken pass through."""
    r = _FakeRedis()
    r.set(f"health:latency:{PRIMARY_SPOT_EXCHANGE}", "900")
    decision = route_for_execution(r, _opp(long_exchange=FALLBACK_SPOT_EXCHANGE))
    assert decision.failed_over is False
    assert decision.opportunity.long_exchange == FALLBACK_SPOT_EXCHANGE


def test_missing_health_signal_is_unhealthy() -> None:
    """No health key for the primary should be treated as unhealthy
    (fail-closed, matches the risk-engine exchange_health rule)."""
    r = _FakeRedis()
    r.set(f"health:latency:{FALLBACK_SPOT_EXCHANGE}", "50")
    decision = route_for_execution(r, _opp())
    assert decision.failed_over is True
    assert decision.opportunity.long_exchange == FALLBACK_SPOT_EXCHANGE
