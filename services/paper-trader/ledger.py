"""Append paper trades to BigQuery via Pub/Sub.

The paper-trader does not write to BigQuery directly. It publishes
TradeExecuted events to `arb-trade-fills`; trade-ledger is the sole writer
into BigQuery — this keeps the write path single-sourced and auditable.
"""

from __future__ import annotations

import logging

from shared.models.trade import Trade
from shared.pubsub.publisher import Topic, get_publisher

logger = logging.getLogger(__name__)


def append_paper_trade(trade: Trade) -> None:
    publisher = get_publisher()
    publisher.publish(
        Topic.TRADE_FILLS,
        trade,
        attributes={"trade_type": trade.type.value, "status": trade.status.value},
    )
    logger.info(
        "paper trade published id=%s opp=%s net_pnl=%.2f", trade.id, trade.opportunity_id, trade.net_pnl_usd
    )
