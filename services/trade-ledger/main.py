"""trade-ledger — Pub/Sub → BigQuery.

Subscribes to:
  arb-trade-fills      → arb_trading.trades
  arb-opportunities    → arb_trading.opportunities
  arb-funding-rates    → arb_trading.funding_events
  arb-risk-alerts      → arb_risk.risk_events
  arb-audit-log        → arb_audit.audit_log
  arb-market-data      → arb_market_data.ticks   (opt-in; ENABLE_TICK_COLLECTION)

Exposes:
  GET /healthz
  GET /status
  GET /tax-export?year=YYYY                  — IRS Form 8949 CSV (capital gains)
  GET /tax-export/funding-income?year=YYYY   — Funding payments as ordinary income
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

import writer
from shared.models.exchange_tick import ExchangeTick
from shared.models.funding_rate import FundingRate
from shared.models.opportunity import Opportunity
from shared.models.risk_decision import RiskDecision
from shared.models.trade import Trade
from tax_export import export_funding_income_year, export_year
from tick_collector import TickBuffer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("trade-ledger")


_subscriber_client = None
_futures: list = []
_tick_buffer: TickBuffer | None = None


def _tick_collection_enabled() -> bool:
    return os.environ.get("ENABLE_TICK_COLLECTION", "false").lower() == "true"


def _start_subscribers() -> None:
    global _subscriber_client
    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        logger.warning("GCP_PROJECT_ID unset — trade-ledger will not subscribe (local mode)")
        return

    from google.cloud import pubsub_v1

    _subscriber_client = pubsub_v1.SubscriberClient()

    _subscribe(project_id, "arb-trade-fills-ledger", _on_trade)
    _subscribe(project_id, "arb-opportunities-ledger", _on_opportunity)
    _subscribe(project_id, "arb-funding-rates-ledger", _on_funding)
    _subscribe(project_id, "arb-risk-alerts-ledger", _on_risk_alert)
    _subscribe(project_id, "arb-risk-decisions-ledger", _on_risk_decision)
    _subscribe(project_id, "arb-audit-log-ledger", _on_audit_log)

    # Forward tick collection (opt-in) — high-volume, downsampled + batched,
    # best-effort. Off by default so it never adds BigQuery cost unexpectedly.
    if _tick_collection_enabled():
        global _tick_buffer
        _tick_buffer = TickBuffer(writer.write_ticks)
        _subscribe(project_id, "arb-market-data-ledger", _on_tick)
        logger.info("tick collection ENABLED → arb_market_data.ticks")


def _subscribe(project_id: str, subscription_id: str, callback) -> None:
    assert _subscriber_client is not None
    path = _subscriber_client.subscription_path(project_id, subscription_id)
    future = _subscriber_client.subscribe(path, callback=callback)
    _futures.append(future)
    logger.info("subscribed: %s", path)


def _on_trade(message) -> None:
    try:
        trade = Trade(**json.loads(message.data.decode("utf-8")))
        writer.write_trade(trade)
        message.ack()
    except Exception:
        logger.exception("failed to write trade")
        message.nack()


def _on_opportunity(message) -> None:
    try:
        opp = Opportunity(**json.loads(message.data.decode("utf-8")))
        writer.write_opportunity(opp)
        message.ack()
    except Exception:
        logger.exception("failed to write opportunity")
        message.nack()


def _on_funding(message) -> None:
    try:
        rate = FundingRate(**json.loads(message.data.decode("utf-8")))
        writer.write_funding(rate)
        message.ack()
    except Exception:
        logger.exception("failed to write funding rate")
        message.nack()


def _on_risk_alert(message) -> None:
    try:
        payload = json.loads(message.data.decode("utf-8"))
        writer.write_risk_alert(payload)
        message.ack()
    except Exception:
        logger.exception("failed to write risk alert")
        message.nack()


def _on_risk_decision(message) -> None:
    try:
        decision = RiskDecision(**json.loads(message.data.decode("utf-8")))
        writer.write_risk_decision(decision)
        message.ack()
    except Exception:
        logger.exception("failed to write risk decision")
        message.nack()


def _on_audit_log(message) -> None:
    try:
        payload = json.loads(message.data.decode("utf-8"))
        writer.write_audit_log(payload)
        message.ack()
    except Exception:
        logger.exception("failed to write audit log")
        message.nack()


def _on_tick(message) -> None:
    """Buffer a tick for batched persistence. Best-effort: always ack (ticks are
    high-volume and lossy-tolerant — never nack/redeliver them)."""
    import time
    try:
        if _tick_buffer is not None:
            _tick_buffer.add(ExchangeTick(**json.loads(message.data.decode("utf-8"))), time.time())
    except Exception:
        logger.exception("failed to buffer tick")
    finally:
        message.ack()


def _stop_subscribers() -> None:
    for f in _futures:
        f.cancel()
    if _subscriber_client is not None:
        _subscriber_client.close()
    if _tick_buffer is not None:
        import time
        _tick_buffer.flush(time.time())  # persist any buffered ticks on shutdown


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_subscribers()
    try:
        yield
    finally:
        _stop_subscribers()


app = FastAPI(title="trade-ledger", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict[str, str | int]:
    """Return subscriber status."""
    return {
        "subscribers": len(_futures),
        "project_id": os.environ.get("GCP_PROJECT_ID") or "unset",
    }


@app.get("/tax-export")
def tax_export(year: int = Query(ge=2020, le=2100)) -> Response:
    """Return a Form 8949 CSV for the requested tax year (capital gains)."""
    try:
        csv_body = export_year(year)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return Response(
        content=csv_body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="form_8949_{year}.csv"'
        },
    )


@app.get("/tax-export/funding-income")
def tax_export_funding_income(year: int = Query(ge=2020, le=2100)) -> Response:
    """Return a CSV of funding payments collected during ``year``.

    Funding is taxed as ordinary income (Schedule 1, line 8z "Other
    earned income"). Each row is one closed live trade's net funding
    receipts; aggregate downstream in your tax software.
    """
    try:
        csv_body = export_funding_income_year(year)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return Response(
        content=csv_body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="funding_income_{year}.csv"'
        },
    )
