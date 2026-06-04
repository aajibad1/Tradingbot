"""Cross-exchange spot arb — buy where it's cheapest, sell where it's most expensive.

For each asset, find the venue with the lowest ask (we buy there) and the venue
with the highest bid (we sell there). Gross spread = (sell_bid - buy_ask) / buy_ask.

Skips perp instruments entirely — basis/funding are separate strategies.

This strategy does NOT do any fee/slippage/buffer arithmetic. Slippage is
delegated to ``shared.utils.slippage_model`` and the per-exchange fee +
viability math live inside ``shared.utils.fee_calculator.calculate_net_edge``
(called by the scorer).
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
_MIN_GROSS_SPREAD_BPS = 5.0


class CrossExchangeStrategy(Strategy):
    name = "cross_exchange"

    def evaluate(self, snapshot: MarketSnapshot) -> Iterable[ScoredCandidate]:
        candidates: list[ScoredCandidate] = []
        for asset in snapshot.assets_with_ticks():
            ticks = [t for t in snapshot.fresh_ticks_for_asset(asset) if t.instrument_type == "spot"]
            if len(ticks) < 2:
                continue

            buy_at = min(ticks, key=lambda t: t.ask)
            sell_at = max(ticks, key=lambda t: t.bid)
            if buy_at.exchange == sell_at.exchange:
                continue

            try:
                # Skip exchanges we don't have a fee model for — fail loud at
                # the strategy level, not at score time.
                taker_fee_bps(buy_at.exchange)
                taker_fee_bps(sell_at.exchange)
            except KeyError:
                continue

            gross_bps = (sell_at.bid - buy_at.ask) / buy_at.ask * 10_000.0
            if gross_bps < _MIN_GROSS_SPREAD_BPS:
                continue

            slip_bps = estimate_size_tier_bps(_DEFAULT_SIZE_USD)
            candidates.append(
                ScoredCandidate(
                    strategy=StrategyType.CROSS_EXCHANGE,
                    asset=asset,
                    long_exchange=buy_at.exchange,
                    short_exchange=sell_at.exchange,
                    gross_spread_bps=gross_bps,
                    slippage_long_bps=slip_bps,
                    slippage_short_bps=slip_bps,
                    long_reference_price=buy_at.ask,    # we buy the long leg at the ask
                    short_reference_price=sell_at.bid,  # we sell the short leg at the bid
                    recommended_size_usd=_DEFAULT_SIZE_USD,
                    min_hold_hours=0.5,  # spreads close fast on this strategy
                    confidence_score=_confidence(gross_bps),
                )
            )
        return candidates


def _confidence(gross_bps: float) -> float:
    # Saturate at 30 bps spread → high confidence.
    return min(1.0, gross_bps / 30.0)
