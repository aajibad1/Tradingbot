"""Maintain mutable risk state from trade fills.

This closes a critical gap: ``risk:daily_pnl_usd``, ``risk:open_positions``,
``risk:exposure:*`` and ``risk:concentration:*`` were previously only ever
*read* by the drawdown guard and position-limit checks — nothing populated
them, so those loss backstops could never fire. This module subscribes to
``arb-trade-fills`` (the same Trade stream the ledger consumes) and rolls each
fill into the Redis risk state, keeping the risk-engine the sole writer of
``risk:*`` keys.

Semantics per fill:
  * OPEN   → add the position's per-exchange exposure + asset concentration to
             the aggregates and bump the open-position count.
  * CLOSED → release any matching open position (reversing its exposure) and
             realize ``net_pnl_usd`` into the rolling daily PnL. Paper trades
             arrive as a single CLOSED record with no prior OPEN, so the
             release is a no-op and only the PnL is realized — which is exactly
             what the drawdown guard needs.
  * FAILED → release any matching open position; realize nothing.
"""

from __future__ import annotations

import logging

import state as state_module
from shared.models.trade import Trade, TradeStatus

if False:  # TYPE_CHECKING without importing redis at runtime
    from redis import Redis

logger = logging.getLogger("risk-engine.position_tracker")


def _position_notionals(trade: Trade) -> tuple[dict[str, float], float]:
    """Return (per-exchange notional USD, representative position notional USD).

    Each leg's notional is ``size * fill_price``. Per-exchange exposure is the
    leg notional on that venue. The asset-concentration figure uses the larger
    of the two legs (a delta-neutral arb's legs are ~equal), matching how
    position_limits projects a single-side size onto concentration.
    """
    exposure: dict[str, float] = {}
    for leg in trade.legs:
        exposure[leg.exchange] = exposure.get(leg.exchange, 0.0) + leg.size * leg.fill_price
    position_notional = max(exposure.values()) if exposure else 0.0
    return exposure, position_notional


def apply_trade(redis: Redis, trade: Trade) -> None:
    """Fold one trade fill into the Redis risk state. Sole writer of risk:* keys."""
    capital = float(redis.get(state_module.KEY_CAPITAL) or 0.0)
    if capital <= 0:
        # Without capital we cannot express exposure as a pct; realize PnL only
        # (the drawdown guard reads pct, but capital must be set before /evaluate
        # works anyway, so this branch is just defensive for early fills).
        logger.warning("capital unset — skipping exposure update for trade=%s", trade.id)

    if trade.status is TradeStatus.OPEN:
        if capital <= 0:
            return
        notionals, position_notional = _position_notionals(trade)
        exposure_pct = {ex: n / capital * 100.0 for ex, n in notionals.items()}
        concentration_pct = position_notional / capital * 100.0
        asset = trade.legs[0].asset if trade.legs else "UNKNOWN"
        state_module.record_open_position(
            redis, trade.opportunity_id, asset, exposure_pct, concentration_pct
        )
        logger.info(
            "position opened opp=%s exposure_pct=%s concentration_pct=%.3f",
            trade.opportunity_id, exposure_pct, concentration_pct,
        )
        return

    # CLOSED or FAILED — unwind any tracked exposure first.
    released = state_module.release_position(redis, trade.opportunity_id)

    if trade.status is TradeStatus.CLOSED:
        new_total = state_module.incr_daily_pnl(redis, trade.net_pnl_usd)
        logger.info(
            "trade closed opp=%s net_pnl=%.2f daily_pnl=%.2f released_open=%s",
            trade.opportunity_id, trade.net_pnl_usd, new_total, released,
        )
    else:  # FAILED
        logger.info("trade failed opp=%s released_open=%s", trade.opportunity_id, released)
