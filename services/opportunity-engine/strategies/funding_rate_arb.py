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

                # Gross spread is the per-period funding payment, in bps of notional.
                gross_bps = abs(funding_bps_per_period)
                # Confidence scales with funding APR magnitude, capped at 1.0
                conf = min(1.0, abs(funding.annualized_pct) / 30.0)
                if funding.annualized_pct < 0:
                    conf *= 0.7  # short-spot leg is harder to execute

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
                        funding_rate_annualized_pct=funding.annualized_pct,
                        recommended_size_usd=_DEFAULT_SIZE_USD,
                        min_hold_hours=max(4.0, funding.period_hours),
                        confidence_score=conf,
                    )
                )
        return candidates
