"""Daily-loss drawdown guard. Trips the kill switch when breached."""

from shared.models.risk_state import RiskRule, RiskState, RiskViolation
from shared.utils.fee_calculator import MIN_VIABLE_NET_EDGE_BPS


DEFAULT_LIMITS: dict[str, float] = {
    "max_daily_loss_pct": 1.0,
    "max_per_trade_capital_pct": 2.0,
    "max_exchange_exposure_pct": 25.0,
    "max_single_asset_exposure_pct": 20.0,
    "max_leverage_multiplier": 1.0,
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
