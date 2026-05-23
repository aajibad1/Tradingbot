"""Liquidation-distance monitor for open perp positions.

Open perp positions are registered in Redis by the execution-orchestrator's
position-reconciler. Each row carries the current mark price and the
exchange-reported liquidation price. This rule:

  * Computes the distance (as a fraction of mark) to liquidation for each
    open perp.
  * Returns one RiskViolation per position inside the ALERT band.
  * If ANY position is inside the CRITICAL band, the kill switch is tripped
    synchronously (mirrors the drawdown-guard behaviour).

The rule deliberately ALWAYS returns an empty list when no positions are
registered — absence of data is not failure. The position-reconciler is the
authoritative writer of ``risk:positions:open`` (a Redis hash keyed by
opportunity_id, JSON-encoded value). The risk-engine never writes those
keys; only the orchestrator does.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shared.models.risk_state import RiskRule, RiskViolation

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


# Distance to liquidation as a fraction of mark price.
ALERT_THRESHOLD_PCT = 0.15   # within 15% → warn
CRITICAL_THRESHOLD_PCT = 0.05  # within 5%  → kill-switch

POSITIONS_REDIS_KEY = "risk:positions:open"


@dataclass(frozen=True)
class LiquidationCheck:
    """One row of the liquidation-distance scan."""

    opportunity_id: str
    exchange: str
    asset: str
    mark_price: float
    liquidation_price: float
    distance_pct: float
    severity: str  # 'ok' | 'warn' | 'critical'

    def message(self) -> str:
        return (
            f"liquidation distance {self.distance_pct:.1%} for "
            f"{self.exchange}/{self.asset} (mark={self.mark_price:.2f}, "
            f"liq={self.liquidation_price:.2f}, opp_id={self.opportunity_id})"
        )


def _classify(distance_pct: float) -> str:
    if distance_pct < CRITICAL_THRESHOLD_PCT:
        return "critical"
    if distance_pct < ALERT_THRESHOLD_PCT:
        return "warn"
    return "ok"


def _load_positions(redis: Redis) -> list[dict]:
    raw = redis.hgetall(POSITIONS_REDIS_KEY)
    out: list[dict] = []
    for value in raw.values():
        try:
            out.append(json.loads(value))
        except (TypeError, ValueError):
            logger.exception("malformed position row in %s", POSITIONS_REDIS_KEY)
    return out


def scan(redis: Redis) -> list[LiquidationCheck]:
    """Return one LiquidationCheck per open perp position. Empty when none."""
    checks: list[LiquidationCheck] = []
    for row in _load_positions(redis):
        try:
            mark_price = float(row.get("mark_price") or 0.0)
            liq_price = float(row.get("liquidation_price") or 0.0)
        except (TypeError, ValueError):
            continue
        if mark_price <= 0 or liq_price <= 0:
            # Spot positions or rows without a liquidation price — skip.
            continue
        distance_pct = abs(mark_price - liq_price) / mark_price
        checks.append(
            LiquidationCheck(
                opportunity_id=str(row.get("opportunity_id", "")),
                exchange=str(row.get("exchange", "")),
                asset=str(row.get("asset", "")),
                mark_price=mark_price,
                liquidation_price=liq_price,
                distance_pct=distance_pct,
                severity=_classify(distance_pct),
            )
        )
    return checks


def check_liquidations(redis: Redis) -> tuple[list[RiskViolation], list[LiquidationCheck]]:
    """Pipeline-friendly wrapper: returns (violations, all_checks).

    ``violations`` includes every position in the warn band or worse — the
    caller decides whether to escalate. Critical positions are intended to
    trigger the kill switch *synchronously*; we leave that trigger to the
    caller so this function stays read-only (parallels other risk rules).
    """
    all_checks = scan(redis)
    violations: list[RiskViolation] = []
    for c in all_checks:
        if c.severity == "ok":
            continue
        violations.append(
            RiskViolation(
                rule=RiskRule.KILL_SWITCH if c.severity == "critical" else RiskRule.LEVERAGE_LIMIT,
                observed=c.distance_pct * 100.0,
                limit=ALERT_THRESHOLD_PCT * 100.0,
                message=c.message(),
            )
        )
    return violations, all_checks
