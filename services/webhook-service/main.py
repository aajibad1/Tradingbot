"""webhook-service — partner webhook delivery for API-plane events (docs/06 §Webhooks).

Partners register endpoints and receive HMAC-signed deliveries of funding.* /
payout.* / settlement.* events. The delivered body follows the doc-06 webhook
payload standard ({id,type,created_at,tenant_id,correlation_id,data}); each
delivery is signed (signing.py) and carries the event_id as Idempotency-Key so a
retried delivery is dedupable by the receiver.

Two ingestion doors share the delivery logic:
  - Pub/Sub subscriber on FUNDING_EVENTS + PAYOUT_EVENTS (gated; skipped local), and
  - POST /v1/events/deliver (an EventEnvelope) — for tests / push delivery.

Endpoints (docs/06):
  GET  /healthz
  POST /v1/webhooks/endpoints                 register (returns the secret ONCE)
  GET  /v1/webhooks/endpoints                 list (secrets redacted)
  POST /v1/webhooks/endpoints/{id}/rotate-secret
  POST /v1/webhooks/endpoints/{id}/replay     re-deliver a prior event ({event_id})
  POST /v1/events/deliver                     fan out an envelope to matching endpoints
  GET  /v1/deliveries                         delivery log
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Request

import signing
from models import Delivery, Endpoint, EndpointCreate
from shared.http import APIError, install_contract
from shared.pubsub.publisher import Topic, pubsub_project_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("webhook-service")

PRODUCER = "webhook-service"
DELIVERY_TIMEOUT_S = 5.0

# In-memory state (single-instance sandbox). Prod: Redis/SQL.
_endpoints: dict[str, Endpoint] = {}
_deliveries: list[Delivery] = []
_seen_events: dict[str, dict[str, Any]] = {}  # event_id -> envelope (for replay)

_subscriber_client = None
_futures: list = []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _webhook_body(envelope: dict[str, Any]) -> dict[str, Any]:
    """The doc-06 webhook payload standard, built from an event envelope."""
    return {
        "id": envelope.get("event_id"),
        "type": envelope.get("event_type"),
        "created_at": envelope.get("occurred_at"),
        "tenant_id": envelope.get("tenant_id"),
        "correlation_id": envelope.get("correlation_id"),
        "data": envelope.get("payload") or {},
    }


def _deliver_to(endpoint: Endpoint, envelope: dict[str, Any]) -> Delivery:
    """Sign + POST one envelope to one endpoint, recording the attempt."""
    body = json.dumps(_webhook_body(envelope)).encode("utf-8")
    ts = str(int(_now().timestamp()))
    headers = {
        "content-type": "application/json",
        signing.SIGNATURE_HEADER: signing.sign(endpoint.secret or "", ts, body),
        signing.TIMESTAMP_HEADER: ts,
        "Idempotency-Key": envelope.get("event_id", ""),
    }
    now = _now()
    delivery = Delivery(
        id=_new_id("dlv"), endpoint_id=endpoint.id,
        event_id=envelope.get("event_id", ""), event_type=envelope.get("event_type", ""),
        status="failed", attempts=1, created_at=now, updated_at=now,
    )
    try:
        resp = httpx.post(endpoint.url, content=body, headers=headers, timeout=DELIVERY_TIMEOUT_S)
        delivery.response_code = resp.status_code
        delivery.status = "delivered" if 200 <= resp.status_code < 300 else "failed"
    except Exception as exc:  # noqa: BLE001 — record the failure, allow replay
        logger.warning("webhook delivery failed endpoint=%s event=%s: %s",
                       endpoint.id, delivery.event_id, exc)
    _deliveries.append(delivery)
    return delivery


def deliver_envelope(envelope: dict[str, Any]) -> list[Delivery]:
    """Fan an envelope out to every matching, active endpoint."""
    event_type = envelope.get("event_type", "")
    event_id = envelope.get("event_id")
    if event_id:
        _seen_events[event_id] = envelope
    out = []
    for ep in _endpoints.values():
        if ep.matches(event_type):
            out.append(_deliver_to(ep, envelope))
    return out


def _on_event(message) -> None:
    try:
        deliver_envelope(json.loads(message.data.decode("utf-8")))
        message.ack()
    except Exception:
        logger.exception("failed to deliver webhook event")
        message.nack()


def _start_subscribers() -> None:
    global _subscriber_client
    project_id = pubsub_project_id()
    if not project_id:
        logger.warning("Pub/Sub disabled — webhook-service running without subscriber (local mode)")
        return
    from google.cloud import pubsub_v1

    _subscriber_client = pubsub_v1.SubscriberClient()
    for topic, sub in (
        (Topic.FUNDING_EVENTS, "funding-events-webhook"),
        (Topic.PAYOUT_EVENTS, "payout-events-webhook"),
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


app = FastAPI(title="webhook-service", version="0.1.0", lifespan=lifespan)
install_contract(app, service_name=PRODUCER)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/webhooks/endpoints", response_model=Endpoint)
def create_endpoint(req: EndpointCreate) -> Endpoint:
    ep = Endpoint(
        id=_new_id("we"), url=req.url, tenant_id=req.tenant_id,
        event_types=req.event_types, active=True, created_at=_now(),
        secret=signing.new_secret(),
    )
    _endpoints[ep.id] = ep
    # Return WITH the secret — the only time it is ever exposed.
    return ep


@app.get("/v1/webhooks/endpoints")
def list_endpoints() -> dict[str, Any]:
    return {"endpoints": [ep.redacted().model_dump() for ep in _endpoints.values()]}


def _get_endpoint(endpoint_id: str) -> Endpoint:
    ep = _endpoints.get(endpoint_id)
    if ep is None:
        raise APIError("endpoint_not_found", f"endpoint {endpoint_id} not found", http_status=404)
    return ep


@app.post("/v1/webhooks/endpoints/{endpoint_id}/rotate-secret", response_model=Endpoint)
def rotate_secret(endpoint_id: str) -> Endpoint:
    ep = _get_endpoint(endpoint_id)
    ep.secret = signing.new_secret()
    _endpoints[endpoint_id] = ep
    return ep  # returns the new secret once


@app.post("/v1/webhooks/endpoints/{endpoint_id}/replay")
def replay(endpoint_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Re-deliver a previously-seen event to this endpoint ({"event_id": ...})."""
    ep = _get_endpoint(endpoint_id)
    event_id = body.get("event_id")
    envelope = _seen_events.get(event_id) if event_id else None
    if envelope is None:
        raise APIError("event_not_found", f"event {event_id} not seen — cannot replay", http_status=404)
    delivery = _deliver_to(ep, envelope)
    return {"delivery": delivery.model_dump()}


@app.post("/v1/events/deliver")
async def deliver(request: Request) -> dict[str, Any]:
    envelope = await request.json()
    deliveries = deliver_envelope(envelope)
    return {
        "delivered": [d.model_dump() for d in deliveries if d.status == "delivered"],
        "failed": [d.model_dump() for d in deliveries if d.status == "failed"],
        "matched": len(deliveries),
    }


@app.get("/v1/deliveries")
def list_deliveries() -> dict[str, Any]:
    return {"deliveries": [d.model_dump() for d in _deliveries], "count": len(_deliveries)}
