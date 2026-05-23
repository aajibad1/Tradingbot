"""Exchange health checks: API latency, rate-limit headroom.

Latency is sampled by the market-data service and pushed into Redis under
`health:latency:<exchange>` (ms, float). The risk engine reads — it does NOT
probe exchanges directly (that would duplicate connections and burn rate limit).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.models.risk_state import RiskRule, RiskViolation

if TYPE_CHECKING:
    from redis import Redis


PREFIX_LATENCY = "health:latency:"


def check_exchange_health(
    redis: Redis,
    exchanges: list[str],
    max_latency_ms: float,
) -> list[RiskViolation]:
    violations: list[RiskViolation] = []
    for exchange in exchanges:
        raw = redis.get(f"{PREFIX_LATENCY}{exchange}")
        if raw is None:
            # Missing health signal is itself a violation — fail closed.
            violations.append(
                RiskViolation(
                    rule=RiskRule.API_LATENCY,
                    observed=-1.0,
                    limit=max_latency_ms,
                    message=f"no latency signal for {exchange} — health unknown",
                )
            )
            continue
        latency = float(raw)
        if latency > max_latency_ms:
            violations.append(
                RiskViolation(
                    rule=RiskRule.API_LATENCY,
                    observed=latency,
                    limit=max_latency_ms,
                    message=(
                        f"{exchange} latency {latency:.0f}ms exceeds max {max_latency_ms:.0f}ms"
                    ),
                )
            )
    return violations
