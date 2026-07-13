"""Daily-loss drawdown guard. Trips the kill switch when breached."""

import math
import os

from shared.models.risk_state import RiskRule, RiskState, RiskViolation
from shared.utils.fee_calculator import MIN_VIABLE_NET_EDGE_BPS


def _sleeve_pct_from_env() -> float:
    """Parse MAX_DIRECTIONAL_EXPOSURE_PCT, failing the BOOT on anything that is
    not a finite percentage >= 0. float() alone accepts "nan"/"inf", and a NaN
    limit silently disables the cap (every `projected > limit` comparison is
    False) — the one class of bad input that would boot cleanly and neutralize
    the control. Fail-to-boot is deliberate: on Cloud Run a crashing revision
    never takes traffic, so a typo leaves the previous good config serving."""
    raw = os.environ.get("MAX_DIRECTIONAL_EXPOSURE_PCT", "0.0")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"MAX_DIRECTIONAL_EXPOSURE_PCT={raw!r} is not a valid percentage")
    if not math.isfinite(value) or value < 0:
        raise ValueError(
            f"MAX_DIRECTIONAL_EXPOSURE_PCT={raw!r} must be a finite percentage >= 0")
    return value


DEFAULT_LIMITS: dict[str, float] = {
    "max_daily_loss_pct": 1.0,
    "max_per_trade_capital_pct": 2.0,
    "max_exchange_exposure_pct": 25.0,
    "max_single_asset_exposure_pct": 20.0,
    "max_leverage_multiplier": 1.0,
    # Hybrid directional sleeve budget (% of capital). 0.0 = pure market-neutral
    # (directional opportunities are rejected). Operator knob: the
    # MAX_DIRECTIONAL_EXPOSURE_PCT env raises it to enable the satellite sleeve.
    # Read once at import — changing it requires a config DEPLOY, deliberately:
    # risk-limit changes are an operator action, never a runtime/UI bypass
    # (docs/RISK_POLICY.md; the AI permission model may only PROPOSE them).
    "max_directional_exposure_pct": _sleeve_pct_from_env(),
    # Anchored to the same constant the opportunity-engine uses; never let
    # these two drift. ``min_viable_net_edge_bps()`` in shared/utils picks up
    # Redis-backed dynamic overrides; here we keep the static floor.
    "min_net_edge_bps": MIN_VIABLE_NET_EDGE_BPS,
    "max_api_latency_ms": 500.0,
}


def check_drawdown(state: RiskState, limits: dict[str, float]) -> RiskViolation | None:
    max_loss = limits["max_daily_loss_pct"]
    if state.daily_loss_pct > max_loss:
        return RiskViolation(
            rule=RiskRule.DAILY_LOSS_LIMIT,
            observed=state.daily_loss_pct,
            limit=max_loss,
            message=(
                f"daily loss {state.daily_loss_pct:.2f}% exceeds max {max_loss:.2f}% — "
                "kill switch should trigger"
            ),
        )
    return None
