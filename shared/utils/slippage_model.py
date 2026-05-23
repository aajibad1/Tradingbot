"""Order-book aware slippage estimation.

Used by both the opportunity-engine (pre-trade) and the paper-trader simulator
(execution model). Returns slippage in bps for a taker order of `size_usd`
against a top-N order book snapshot.

Two helpers live here:

  * ``estimate_slippage_bps`` — book-aware, walks the asks/bids of a real
    ``OrderBookSnapshot`` to derive an exact average-fill slippage. Preferred
    whenever a fresh L2 snapshot is available.
  * ``estimate_size_tier_bps`` — a calibrated fall-back used by the
    opportunity-engine strategies when no L2 snapshot is in hand. It linearly
    interpolates between three reference points calibrated from historical
    CEX fills:

        $10,000  →   2.0 bps
        $50,000  →   5.0 bps
        $100,000 →  12.0 bps

    Sizes below $10K are clamped to the $10K rate; sizes above $100K
    extrapolate using the slope between the $50K and $100K anchors (the
    book gets thin faster up there).
"""

from shared.models.exchange_tick import OrderBookSnapshot

# Calibrated fallback bps when order book depth is unavailable.
# Linear in trade size: 1 bp / $10K, capped.
_FALLBACK_BPS_PER_10K = 1.0
_FALLBACK_BPS_CAP = 25.0


# Size-tier anchors (size_usd, slippage_bps). Sorted ascending by size.
_SIZE_TIER_ANCHORS: list[tuple[float, float]] = [
    (10_000.0, 2.0),
    (50_000.0, 5.0),
    (100_000.0, 12.0),
]


def estimate_slippage_bps(
    size_usd: float,
    side: str,
    snapshot: OrderBookSnapshot | None,
) -> float:
    """Estimate execution slippage for a taker order in bps.

    side: 'buy' walks the asks, 'sell' walks the bids.
    """
    if size_usd <= 0:
        return 0.0

    if snapshot is None:
        return min(_FALLBACK_BPS_CAP, _FALLBACK_BPS_PER_10K * (size_usd / 10_000.0))

    levels = snapshot.asks if side == "buy" else snapshot.bids
    if not levels:
        return _FALLBACK_BPS_CAP

    reference_price = levels[0].price
    if reference_price <= 0:
        return _FALLBACK_BPS_CAP

    remaining_usd = size_usd
    filled_notional = 0.0
    filled_qty = 0.0

    for level in levels:
        level_notional = level.price * level.size
        take_notional = min(remaining_usd, level_notional)
        take_qty = take_notional / level.price
        filled_notional += take_notional
        filled_qty += take_qty
        remaining_usd -= take_notional
        if remaining_usd <= 0:
            break

    if filled_qty == 0:
        return _FALLBACK_BPS_CAP

    if remaining_usd > 0:
        # Book too shallow — cap and assume we wouldn't trade.
        return _FALLBACK_BPS_CAP

    avg_fill_price = filled_notional / filled_qty
    slippage_pct = abs(avg_fill_price - reference_price) / reference_price
    return slippage_pct * 10_000.0


def estimate_size_tier_bps(size_usd: float) -> float:
    """Calibrated size-tier slippage estimate (no order book required).

    Used by opportunity-engine strategies as a pre-trade heuristic when no
    fresh L2 snapshot is in the in-memory MarketSnapshot. Returns bps.

    Behaviour:
      * size_usd <= $10K  → 2.0 bps (floor)
      * $10K - $50K       → linear interpolation between 2 and 5 bps
      * $50K - $100K      → linear interpolation between 5 and 12 bps
      * size_usd > $100K  → linear extrapolation along the 50K-100K slope,
                             capped at ``_FALLBACK_BPS_CAP``
      * size_usd <= 0     → 0.0
    """
    if size_usd <= 0:
        return 0.0

    if size_usd <= _SIZE_TIER_ANCHORS[0][0]:
        return _SIZE_TIER_ANCHORS[0][1]

    for (lo_size, lo_bps), (hi_size, hi_bps) in zip(
        _SIZE_TIER_ANCHORS, _SIZE_TIER_ANCHORS[1:]
    ):
        if size_usd <= hi_size:
            frac = (size_usd - lo_size) / (hi_size - lo_size)
            return lo_bps + frac * (hi_bps - lo_bps)

    # Above the largest anchor: extrapolate along the last segment's slope.
    (lo_size, lo_bps), (hi_size, hi_bps) = _SIZE_TIER_ANCHORS[-2], _SIZE_TIER_ANCHORS[-1]
    slope = (hi_bps - lo_bps) / (hi_size - lo_size)
    extrapolated = hi_bps + slope * (size_usd - hi_size)
    return min(_FALLBACK_BPS_CAP, extrapolated)
