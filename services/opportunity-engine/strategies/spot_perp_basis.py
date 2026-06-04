"""Spot-perp basis trade.

When a perp trades at a meaningful premium/discount to spot, we capture the
basis: buy the cheaper leg, short the more expensive leg, hold until the basis
narrows. Direction is determined by which leg is over/underpriced.

Funding works in our favor or against us depending on which side we short —
the scorer's ``funding_rate_bps_per_period`` term handles that.

This strategy does NOT do any fee/slippage/buffer arithmetic. All viability
math is centralized in ``shared.utils.fee_calculator.calculate_net_edge``.
"""

from __future__ import annotations

from collections.abc import Iterable

from models import MarketSnapshot
from scorer import ScoredCandidate
from shared.models.opportunity import StrategyType
from shared.utils.fee_calculator import taker_fee_bps
from shared.utils.slippage_model import estimate_size_tier_bps
from strategies.base import Strategy


_DEFAULT_SIZE_USD = 10_000.0
_MIN_BASIS_BPS = 5.0


class SpotPerpBasisStrategy(Strategy):
    name = "spot_perp_basis"

    def evaluate(self, snapshot: MarketSnapshot) -> Iterable[ScoredCandidate]:
        candidates: list[ScoredCandidate] = []
        for asset in snapshot.assets_with_ticks():
            ticks = snapshot.fresh_ticks_for_asset(asset)
            spots = [t for t in ticks if t.instrument_type == "spot"]
            perps = [t for t in ticks if t.instrument_type == "perp"]
            if not spots or not perps:
                continue

            funding_by_exchange = {
                f.exchange: f for f in snapshot.fresh_funding_for_asset(asset)
            }
            slip_bps = estimate_size_tier_bps(_DEFAULT_SIZE_USD)

            for spot in spots:
                for perp in perps:
                    try:
                        taker_fee_bps(spot.exchange)
                        taker_fee_bps(perp.exchange)
                    except KeyError:
                        continue

                    spot_mid = (spot.bid + spot.ask) / 2
                    perp_mid = (perp.bid + perp.ask) / 2
                    basis_bps = (perp_mid - spot_mid) / spot_mid * 10_000.0

                    if abs(basis_bps) < _MIN_BASIS_BPS:
                        continue

                    funding = funding_by_exchange.get(perp.exchange)
                    funding_apr = funding.annualized_pct if funding else 0.0

                    if basis_bps > 0:
                        # Perp expensive → long spot, short perp.
                        long_ex, short_ex = spot.exchange, perp.exchange
                        long_price, short_price = spot.ask, perp.bid
                        # Short perp collects positive funding when funding > 0.
                        funding_bps_per_period = (
                            funding.rate_per_period * 10_000.0 if funding else 0.0
                        )
                    else:
                        long_ex, short_ex = perp.exchange, spot.exchange
                        long_price, short_price = perp.ask, spot.bid
                        # Long perp pays positive funding — sign flips.
                        funding_bps_per_period = (
                            -funding.rate_per_period * 10_000.0 if funding else 0.0
                        )

                    candidates.append(
                        ScoredCandidate(
                            strategy=StrategyType.SPOT_PERP_BASIS,
                            asset=asset,
                            long_exchange=long_ex,
                            short_exchange=short_ex,
                            gross_spread_bps=abs(basis_bps),
                            slippage_long_bps=slip_bps,
                            slippage_short_bps=slip_bps,
                            long_reference_price=long_price,
                            short_reference_price=short_price,
                            funding_rate_bps_per_period=funding_bps_per_period,
                            funding_rate_annualized_pct=funding_apr,
                            recommended_size_usd=_DEFAULT_SIZE_USD,
                            min_hold_hours=4.0,
                            confidence_score=min(1.0, abs(basis_bps) / 30.0),
                        )
                    )
        return candidates
