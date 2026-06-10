"""settlement-status — normalized settlement read model over funding/payout events.

Consumes the on-ramp (funding.*) and off-ramp (payout.*) lifecycle events and
projects them into one normalized settlement record per order (docs/05). Partners
poll this for a single status vocabulary across both directions; the underlying
on/off-ramp services own the money path, this owns the read view.

Event ingestion has two doors that share the same projection logic:
  - Pub/Sub subscriber on FUNDING_EVENTS + PAYOUT_EVENTS (gated on pubsub config;
    skipped in pure-local mode, like the rest of the wedge), and
  - POST /v1/events/ingest (an EventEnvelope) — used by tests and any push delivery.

Endpoints (docs/06):
  GET  /healthz
  POST /v1/events/ingest
  GET  /v1/settlements/{id}
  GET  /v1/settlements?status=&tenant=
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request

import projection
from shared.http import APIError, install_contract
from shared.pubsub.publisher import Topic, pubsub_project_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("settlement-status")

PRODUCER = "settlement-status"

# Normalized settlement records, keyed by order id (in-memory; prod: Redis/SQL).
_settlements: dict[str, dict[str, Any]] = {}

_subscriber_client = None
_futures: list = []


def ingest_envelope(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Fold one event envelope into the settlement store. Returns the updated
    record, or None if it wasn't a settlement event."""
    order_id = (envelope.get("payload") or {}).get("id")
    prev = _settlements.get(order_id) if order_id else None
    record = projection.project(prev, envelope)
    if record is None:
        return None
    _settlements[record["id"]] = record
    return record


def _on_event(message) -> None:
    try:
        ingest_envelope(json.loads(message.data.decode("utf-8")))
        message.ack()
    except Exception:
        logger.exception("failed to ingest settlement event")
        message.nack()


def _start_subscribers() -> None:
    global _subscriber_client
    project_id = pubsub_project_id()
    if not project_id:
        logger.warning("Pub/Sub disabled — settlement-status running without subscriber (local mode)")
        return
    from google.cloud import pubsub_v1

    _subscriber_client = pubsub_v1.SubscriberClient()
    for topic, sub in (
        (Topic.FUNDING_EVENTS, "funding-events-settlement"),
        (Topic.PAYOUT_EVENTS, "payout-events-settlement"),
    ):
        path = _subscriber_client.subscription_path(project_id, sub)
        _futures.append(_subscriber_client.subscribe(path, callback=_on_event))
        logger.info("subscribed: %s", path)


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


app = FastAPI(title="settlement-status", version="0.1.0", lifespan=lifespan)
install_contract(app, service_name=PRODUCER)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/events/ingest")
async def ingest(request: Request) -> dict[str, Any]:
    """Ingest one EventEnvelope (funding.* / payout.*). Non-settlement events are
    accepted and ignored so a broad push subscription is harmless."""
    envelope = await request.json()
    record = ingest_envelope(envelope)
    return {"ingested": record is not None, "settlement": record}


@app.get("/v1/settlements/{order_id}")
def get_settlement(order_id: str) -> dict[str, Any]:
    record = _settlements.get(order_id)
    if record is None:
        raise APIError("settlement_not_found", f"settlement {order_id} not found", http_status=404)
    return record


@app.get("/v1/settlements")
def list_settlements(status: str | None = None, tenant: str | None = None) -> dict[str, Any]:
    out = list(_settlements.values())
    if status is not None:
        out = [r for r in out if r.get("status") == status]
    if tenant is not None:
        out = [r for r in out if r.get("tenant_id") == tenant]
    return {"settlements": out, "count": len(out)}
