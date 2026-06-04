"""Pub/Sub → BigQuery streaming insert.

Each Pub/Sub topic maps to a BigQuery table. The writer:
  1. Decodes the Pub/Sub message into a Pydantic model
  2. Flattens it into a BigQuery row dict
  3. Streams via `insert_rows_json` (per-row errors logged but not retried —
     duplicate inserts are deduplicated by primary-key downstream)

Row construction is intentionally explicit per table so a model change in
shared/ doesn't silently corrupt the schema.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from shared.models.exchange_tick import ExchangeTick
from shared.models.funding_rate import FundingRate
from shared.models.opportunity import Opportunity
from shared.models.trade import Trade

if TYPE_CHECKING:
    from google.cloud import bigquery

logger = logging.getLogger(__name__)


_PROJECT = os.environ.get("GCP_PROJECT_ID")


def _client() -> bigquery.Client:
    from google.cloud import bigquery

    return bigquery.Client(project=_PROJECT)


def _table(dataset: str, table: str) -> str:
    if not _PROJECT:
        raise RuntimeError("GCP_PROJECT_ID unset")
    return f"{_PROJECT}.{dataset}.{table}"


def _stream(table_id: str, row: dict, row_id: str | None = None) -> None:
    """Audit-critical single-row insert.

    Passes ``row_id`` as the BigQuery dedup key (best-effort de-duplication
    within the streaming window) so at-least-once Pub/Sub redelivery doesn't
    create duplicate rows. RAISES on insert errors so the Pub/Sub callback
    nacks and the message is redelivered — never silently drop an audit row.
    """
    client = _client()
    row_ids = [row_id] if row_id is not None else None
    errors = client.insert_rows_json(table_id, [row], row_ids=row_ids)
    if errors:
        raise RuntimeError(f"bigquery insert failed {table_id}: {errors}")


def _stream_many(table_id: str, rows: list[dict]) -> None:
    """Batch streaming insert. Best-effort (logs, never raises) — used for the
    high-volume, lossy-tolerant tick stream, NOT the audit-critical tables."""
    if not rows:
        return
    client = _client()
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        logger.error("bigquery tick insert errors %s: %d rows, first=%s",
                     table_id, len(rows), errors[:1])


def trade_to_row(trade: Trade) -> dict:
    total_fees = sum(leg.fee_usd for leg in trade.legs)
    total_slippage = sum(leg.slippage_usd for leg in trade.legs)
    long_exchange = next((leg.exchange for leg in trade.legs if leg.side == "buy"), None)
    short_exchange = next((leg.exchange for leg in trade.legs if leg.side == "sell"), None)
    asset = trade.legs[0].asset if trade.legs else ""
    return {
        "trade_id": trade.id,
        "opportunity_id": trade.opportunity_id,
        "trade_type": trade.type.value,
        "status": trade.status.value,
        "asset": asset,
        "long_exchange": long_exchange,
        "short_exchange": short_exchange,
        "gross_pnl_usd": trade.gross_pnl_usd,
        "net_pnl_usd": trade.net_pnl_usd,
        "total_fees_usd": total_fees,
        "total_slippage_usd": total_slippage,
        "funding_collected_usd": trade.funding_collected_usd,
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
        "notes": trade.notes,
        "legs": [
            {
                "exchange": leg.exchange,
                "side": leg.side,
                "asset": leg.asset,
                "size": leg.size,
                "fill_price": leg.fill_price,
                "fee_usd": leg.fee_usd,
                "slippage_usd": leg.slippage_usd,
                "filled_at": leg.filled_at.isoformat(),
            }
            for leg in trade.legs
        ],
    }


def opportunity_to_row(opp: Opportunity) -> dict:
    return {
        "opportunity_id": opp.id,
        "strategy": opp.strategy.value,
        "asset": opp.asset,
        "long_exchange": opp.long_exchange,
        "short_exchange": opp.short_exchange,
        "gross_spread_bps": opp.gross_spread_bps,
        "trading_fees_bps": opp.trading_fees_bps,
        "slippage_estimate_bps": opp.slippage_estimate_bps,
        "funding_rate_annualized_pct": opp.funding_rate_annualized_pct,
        "net_edge_bps": opp.net_edge_bps,
        "confidence_score": opp.confidence_score,
        "recommended_size_usd": opp.recommended_size_usd,
        "min_hold_hours": opp.min_hold_hours,
        "detected_at": opp.detected_at.isoformat(),
        "execute": opp.execute,
        "rejection_reason": opp.rejection_reason,
    }


def funding_to_row(rate: FundingRate) -> dict:
    return {
        "exchange": rate.exchange,
        "asset": rate.asset,
        "symbol": rate.symbol,
        "rate_per_period": rate.rate_per_period,
        "period_hours": rate.period_hours,
        "annualized_pct": rate.annualized_pct,
        "next_funding_time": rate.next_funding_time.isoformat() if rate.next_funding_time else None,
        "observed_at": rate.observed_at.isoformat(),
        "source": rate.source,
    }


def risk_alert_to_row(payload: dict) -> dict:
    """Risk alerts come from multiple producers; passed through as raw dicts."""
    return {
        "alert_type": payload["alert_type"],
        "severity": payload.get("severity", "info"),
        "message": payload.get("message", ""),
        "rule": payload.get("rule"),
        "observed": payload.get("observed"),
        "limit_value": payload.get("limit"),
        "source": payload.get("source"),
        "emitted_at": payload["emitted_at"],
    }


def audit_log_to_row(payload: dict) -> dict:
    """Audit log entries from all services."""
    return {
        "event_id": payload.get("event_id"),
        "source": payload["source"],
        "event_type": payload["event_type"],
        "actor": payload.get("actor"),
        "action": payload.get("action"),
        "resource_type": payload.get("resource_type"),
        "resource_id": payload.get("resource_id"),
        "metadata": payload.get("metadata"),
        "emitted_at": payload["emitted_at"],
    }


def tick_to_row(tick: ExchangeTick) -> dict:
    return {
        "exchange": tick.exchange,
        "symbol": tick.symbol,
        "asset": tick.asset,
        "bid": tick.bid,
        "ask": tick.ask,
        "bid_size": tick.bid_size,
        "ask_size": tick.ask_size,
        "last": tick.last,
        "instrument_type": tick.instrument_type,
        "observed_at": tick.timestamp.isoformat(),
    }


def write_ticks(rows: list[dict]) -> None:
    """Batch-write downsampled ticks to arb_market_data.ticks (best-effort)."""
    _stream_many(_table("arb_market_data", "ticks"), rows)


def write_trade(trade: Trade) -> None:
    _stream(_table("arb_trading", "trades"), trade_to_row(trade), row_id=trade.id)


def write_opportunity(opp: Opportunity) -> None:
    _stream(_table("arb_trading", "opportunities"), opportunity_to_row(opp), row_id=opp.id)


def write_funding(rate: FundingRate) -> None:
    # No natural id — a venue/asset/timestamp tuple uniquely identifies a reading.
    row_id = f"{rate.exchange}:{rate.asset}:{rate.observed_at.isoformat()}"
    _stream(_table("arb_trading", "funding_events"), funding_to_row(rate), row_id=row_id)


def write_risk_alert(payload: dict) -> None:
    row_id = payload.get("event_id") or f"{payload.get('alert_type')}:{payload['emitted_at']}"
    _stream(_table("arb_risk", "risk_events"), risk_alert_to_row(payload), row_id=row_id)


def write_audit_log(payload: dict) -> None:
    row_id = payload.get("event_id") or f"{payload['source']}:{payload['event_type']}:{payload['emitted_at']}"
    _stream(_table("arb_audit", "audit_log"), audit_log_to_row(payload), row_id=row_id)
