"""Funding-rate arb — delta-neutral carry trade.

When a perp's funding rate is meaningfully positive, short the perp (collect
funding) and long spot on the same asset (neutralize price exposure). The
gross_spread here is the funding rate itself, paid every ``period_hours``.

For negative funding, flip: long perp + short spot (the spot leg requires
margin borrow — only viable on exchanges that support it). This negative-
funding branch is gated by the ``ENABLE_NEGATIVE_FUNDING`` env var because
the short-spot leg has real execution risk on US-domiciled CEXes:

    ENABLE_NEGATIVE_FUNDING=true   → both directions emitted
    ENABLE_NEGATIVE_FUNDING=false  → positive funding only (default)

An optional EMA trend gate suppresses candidates whose funding rate is
currently above the threshold but trending DOWN (i.e. likely to fall
through the threshold mid-hold). The gate is opt-in:

    REQUIRE_FUNDING_UPTREND=true   → only emit when latest > EMA AND > 0
    REQUIRE_FUNDING_UPTREND=false  → emit on raw threshold (default)

This strategy does NOT do any fee/slippage/buffer arithmetic. The scorer
funnels everything through ``shared.utils.fee_calculator.calculate_net_edge``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from models import MarketSnapshot
from scorer import ScoredCandidate
from shared.models.opportunity import StrategyType
from shared.utils.fee_calculator import taker_fee_bps
from shared.utils.slippage_model import estimate_size_tier_bps
from strategies.base import Strategy


_DEFAULT_SIZE_USD = 10_000.0
_MIN_FUNDING_APR_PCT = 8.0
# Carry is a multi-period edge: funding accrues every period over the hold while
# round-trip costs are paid once. The viability math credits funding over this
# planned hold. This is a TRADING ASSUMPTION (how long we expect to hold the
# carry) — conservative default, tunable, and the dominant driver of which
# funding rates clear the net-edge bar.
_DEFAULT_CARRY_HOLD_HOURS = 72.0
# Hard cap: the stress sweep showed that extending the assumed hold to book more
# trades manufactures fake edge — at 14d the modeled edge ran ~10x realized and
# the median trade went negative. Cap the horizon so that trap is structurally
# unreachable regardless of the env override.
_MAX_CARRY_HOLD_HOURS = 72.0

# Credited funding is haircut by this factor for early exit (the position can
# close before the full hold) and funding mean-reversion (later periods earn
# less than the entry rate). Calibrated against the simulator so the modeled
# edge tracks realized PnL rather than running 1.5–10x optimistic. A TRADING
# ASSUMPTION — tunable, conservative by default.
_DEFAULT_FUNDING_PERSISTENCE = 0.6


def _carry_hold_hours() -> float:
    """Assumed carry horizon, clamped to a conservative hard cap."""
    try:
        requested = float(os.environ.get("FUNDING_TARGET_HOLD_HOURS", _DEFAULT_CARRY_HOLD_HOURS))
    except ValueError:
        requested = _DEFAULT_CARRY_HOLD_HOURS
    return min(_MAX_CARRY_HOLD_HOURS, max(1.0, requested))


def _funding_persistence() -> float:
    """Funding haircut in (0, 1]; tunable via env."""
    try:
        return min(1.0, max(0.0, float(os.environ.get("FUNDING_PERSISTENCE", _DEFAULT_FUNDING_PERSISTENCE))))
    except ValueError:
        return _DEFAULT_FUNDING_PERSISTENCE


def _negative_funding_enabled() -> bool:
    """Resolved per-call so tests can flip the flag without re-importing."""
    return os.environ.get("ENABLE_NEGATIVE_FUNDING", "false").lower() == "true"


def _require_uptrend() -> bool:
    """Resolved per-call so tests can flip the flag without re-importing."""
    return os.environ.get("REQUIRE_FUNDING_UPTREND", "false").lower() == "true"


class FundingRateArbStrategy(Strategy):
    name = "funding_rate_arb"

    def evaluate(self, snapshot: MarketSnapshot) -> Iterable[ScoredCandidate]:
        candidates: list[ScoredCandidate] = []
        allow_negative = _negative_funding_enabled()
        require_uptrend = _require_uptrend()
        hold_hours = _carry_hold_hours()
        persistence = _funding_persistence()
        for asset in snapshot.assets_with_ticks():
            funding_rates = snapshot.fresh_funding_for_asset(asset)
            if not funding_rates:
                continue

            spot_ticks = [
                t
                for t in snapshot.fresh_ticks_for_asset(asset)
                if t.instrument_type == "spot"
            ]
            if not spot_ticks:
                continue
            best_spot = min(spot_ticks, key=lambda t: t.ask)
            try:
                taker_fee_bps(best_spot.exchange)
            except KeyError:
                continue

            slip_bps = estimate_size_tier_bps(_DEFAULT_SIZE_USD)

            for funding in funding_rates:
                if abs(funding.annualized_pct) < _MIN_FUNDING_APR_PCT:
                    continue
                try:
                    taker_fee_bps(funding.exchange)
                except KeyError:
                    continue

                if funding.annualized_pct > 0:
                    if require_uptrend and not snapshot.is_funding_trending_positive(
                        funding.exchange, funding.asset
                    ):
                        # Rate is above threshold but trending DOWN — skip.
                        continue
                    long_ex, short_ex = best_spot.exchange, funding.exchange
                    funding_bps_per_period = funding.rate_per_period * 10_000.0
                else:
                    # Negative funding: short-spot leg requires margin support
                    # and is gated by an env flag. Skip silently when disabled.
                    if not allow_negative:
                        continue
                    long_ex, short_ex = funding.exchange, best_spot.exchange
                    funding_bps_per_period = -funding.rate_per_period * 10_000.0

                # Delta-neutral carry: the ENTIRE edge is the per-period funding
                # payment, supplied once below via ``funding_rate_bps_per_period``.
                # A pure carry trade has no price-convergence ("gross spread")
                # component, so gross spread is 0. Putting the funding value into
                # BOTH gross_spread_bps and funding_rate_bps_per_period would make
                # calculate_net_edge (gross + funding - costs) count it twice, which
                # roughly doubles the apparent edge and also makes the paper-trader
                # book a phantom convergence gain on top of accrued funding.
                gross_bps = 0.0
                # Confidence scales with funding APR magnitude, capped at 1.0
                conf = min(1.0, abs(funding.annualized_pct) / 30.0)
                if funding.annualized_pct < 0:
                    conf *= 0.7  # short-spot leg is harder to execute

                # Funding accrues every period over the planned carry hold; the
                # scorer credits funding_per_period * funding_periods against the
                # one-time round-trip cost. period_hours is venue-specific (1h HL,
                # 8h CEX), so the same hold yields more periods on hourly venues.
                funding_periods = max(1, int(hold_hours // funding.period_hours))

                candidates.append(
                    ScoredCandidate(
                        strategy=StrategyType.FUNDING_RATE_ARB,
                        asset=asset,
                        long_exchange=long_ex,
                        short_exchange=short_ex,
                        gross_spread_bps=gross_bps,
                        slippage_long_bps=slip_bps,
                        slippage_short_bps=slip_bps,
                        funding_rate_bps_per_period=funding_bps_per_period,
                        funding_periods=funding_periods,
                        funding_persistence=persistence,
                        funding_rate_annualized_pct=funding.annualized_pct,
                        recommended_size_usd=_DEFAULT_SIZE_USD,
                        min_hold_hours=hold_hours,
                        confidence_score=conf,
                    )
                )
        return candidates
