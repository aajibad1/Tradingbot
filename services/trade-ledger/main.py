"""trade-ledger — Pub/Sub → BigQuery.

Subscribes to:
  arb-trade-fills      → arb_trading.trades
  arb-opportunities    → arb_trading.opportunities
  arb-funding-rates    → arb_trading.funding_events
  arb-risk-alerts      → arb_risk.risk_events
  arb-audit-log        → arb_audit.audit_log

Exposes:
  GET /healthz
  GET /status
  GET /tax-export?year=YYYY     — IRS Form 8949 CSV download (live closed trades only)
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

import writer
from shared.models.funding_rate import FundingRate
from shared.models.opportunity import Opportunity
from shared.models.trade import Trade
from tax_export import export_year

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("trade-ledger")


_subscriber_client = None
_futures: list = []


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
    _subscribe(project_id, "arb-audit-log-ledger", _on_audit_log)


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


def _on_audit_log(message) -> None:
    try:
        payload = json.loads(message.data.decode("utf-8"))
        writer.write_audit_log(payload)
        message.ack()
    except Exception:
        logger.exception("failed to write audit log")
        message.nack()


def _stop_subscribers() -> None:
    for f in _futures:
        f.cancel()
    if _subscriber_client is not None:
        _subscriber_client.close()


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
    """Return a Form 8949 CSV for the requested tax year."""
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
