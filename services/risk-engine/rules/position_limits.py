"""Hard limits on trade size, exchange exposure, and asset concentration."""

from shared.models.opportunity import Opportunity
from shared.models.risk_state import RiskRule, RiskState, RiskViolation


def check_position_limits(
    opportunity: Opportunity,
    state: RiskState,
    limits: dict[str, float],
) -> list[RiskViolation]:
    """Return all violations triggered by this opportunity. Empty list = approved."""
    violations: list[RiskViolation] = []
    capital = state.capital_usd

    size_pct = opportunity.recommended_size_usd / capital * 100.0
    max_size_pct = limits["max_per_trade_capital_pct"]
    if size_pct > max_size_pct:
        violations.append(
            RiskViolation(
                rule=RiskRule.PER_TRADE_SIZE_LIMIT,
                observed=size_pct,
                limit=max_size_pct,
                message=(
                    f"trade size {size_pct:.2f}% exceeds max {max_size_pct:.2f}% of capital"
                ),
            )
        )

    max_exchange_pct = limits["max_exchange_exposure_pct"]
    incremental_pct = size_pct
    for venue in (opportunity.long_exchange, opportunity.short_exchange):
        projected = state.exchange_exposure_pct.get(venue, 0.0) + incremental_pct
        if projected > max_exchange_pct:
            violations.append(
                RiskViolation(
                    rule=RiskRule.EXCHANGE_EXPOSURE_LIMIT,
                    observed=projected,
                    limit=max_exchange_pct,
                    message=(
                        f"projected {venue} exposure {projected:.2f}% exceeds max "
                        f"{max_exchange_pct:.2f}%"
                    ),
                )
            )

    max_asset_pct = limits["max_single_asset_exposure_pct"]
    projected_asset = state.asset_concentration_pct.get(opportunity.asset, 0.0) + size_pct
    if projected_asset > max_asset_pct:
        violations.append(
            RiskViolation(
                rule=RiskRule.ASSET_CONCENTRATION_LIMIT,
                observed=projected_asset,
                limit=max_asset_pct,
                message=(
                    f"projected {opportunity.asset} concentration {projected_asset:.2f}% "
                    f"exceeds max {max_asset_pct:.2f}%"
                ),
            )
        )

    max_lev = limits["max_leverage_multiplier"]
    if state.leverage_multiplier > max_lev:
        violations.append(
            RiskViolation(
                rule=RiskRule.LEVERAGE_LIMIT,
                observed=state.leverage_multiplier,
                limit=max_lev,
                message=f"leverage {state.leverage_multiplier:.2f}x exceeds {max_lev:.2f}x",
            )
        )

    min_edge = limits["min_net_edge_bps"]
    if opportunity.net_edge_bps < min_edge:
        violations.append(
            RiskViolation(
                rule=RiskRule.MIN_NET_EDGE,
                observed=opportunity.net_edge_bps,
                limit=min_edge,
                message=(
                    f"net edge {opportunity.net_edge_bps:.2f} bps below min {min_edge:.2f} bps"
                ),
            )
        )

    return violations
