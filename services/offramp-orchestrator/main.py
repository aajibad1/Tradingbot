"""offramp-orchestrator — stablecoin → fiat payout API (docs/05 API plane, docs/06).

SANDBOX-ONLY mirror of onramp-orchestrator for the payout direction. No real
money moves; real payout rails are gated on money-transmission licensing
(docs/REGULATORY_BRIEF.md). Exercises the same platform contract (correlation +
error model + Idempotency-Key + Status taxonomy) and emits payout.* lifecycle
events via the canonical EventEnvelope.

Endpoints (docs/06):
  GET  /healthz
  POST /v1/offramp/quotes
  POST /v1/offramp/orders                 (idempotent via Idempotency-Key)
  GET  /v1/offramp/orders/{id}
  POST /v1/offramp/orders/{id}/advance    (sandbox lifecycle step; cron-ping)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request

import sandbox_provider as sandbox
from models import Order, OrderRequest, Quote, QuoteRequest
from shared.http import (
    APIError,
    InMemoryIdempotencyStore,
    Status,
    get_correlation_id,
    idempotency_key,
    install_contract,
)
from shared.pubsub.publisher import Topic, get_publisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("offramp-orchestrator")

PRODUCER = "offramp-orchestrator"

app = FastAPI(title="offramp-orchestrator", version="0.1.0")
install_contract(app, service_name=PRODUCER)

# In-memory state (single-instance sandbox). Prod: Redis-backed.
_orders: dict[str, Order] = {}
_idempotency = InMemoryIdempotencyStore()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _emit(event_type: str, order: Order) -> None:
    """Emit a payout lifecycle event wrapped in the canonical envelope (fail-open)."""
    try:
        get_publisher().publish_event(
            Topic.PAYOUT_EVENTS, event_type, order,
            producer=PRODUCER, tenant_id=order.tenant_id,
            correlation_id=order.correlation_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning("payout event %s emit failed for order=%s (non-fatal)", event_type, order.id)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": "sandbox"}


@app.post("/v1/offramp/quotes", response_model=Quote)
def create_quote(req: QuoteRequest) -> Quote:
    fee, rate, dest_amount = sandbox.quote(req.source_asset, req.dest_currency, req.amount)
    return Quote(
        quote_id=_new_id("qt"),
        source_asset=req.source_asset,
        dest_currency=req.dest_currency,
        amount=req.amount,
        fee=fee,
        rate=rate,
        dest_amount=dest_amount,
        expires_at=_now(),
    )


@app.post("/v1/offramp/orders", response_model=Order)
def create_order(req: OrderRequest, request: Request) -> Order:
    key = idempotency_key(request)
    if key is not None:
        cached = _idempotency.get(key)
        if cached is not None:
            return Order.model_validate(cached)

    fee, _rate, dest_amount = sandbox.quote(req.source_asset, req.dest_currency, req.amount)
    now = _now()
    order = Order(
        id=_new_id("pay"),
        status=Status.PENDING,
        source_asset=req.source_asset,
        dest_currency=req.dest_currency,
        amount=req.amount,
        fee=fee,
        dest_amount=dest_amount,
        destination_account=req.destination_account,
        tenant_id=request.headers.get("x-tenant-id"),
        correlation_id=get_correlation_id(request) or _new_id("corr"),
        created_at=now,
        updated_at=now,
    )
    _orders[order.id] = order
    if key is not None:
        _idempotency.put(key, order.model_dump(mode="json"))
    _emit("payout.created", order)
    logger.info("offramp payout created id=%s %.4f%s→%s", order.id, req.amount,
                req.source_asset, req.dest_currency)
    return order


def _get_order(order_id: str) -> Order:
    order = _orders.get(order_id)
    if order is None:
        raise APIError("order_not_found", f"payout {order_id} not found", http_status=404)
    return order


@app.get("/v1/offramp/orders/{order_id}", response_model=Order)
def get_order(order_id: str) -> Order:
    return _get_order(order_id)


@app.post("/v1/offramp/orders/{order_id}/advance", response_model=Order)
def advance_order(order_id: str) -> Order:
    """Advance the sandbox payout one step and emit the payout event.

    pending → processing (payout.processing) → completed (payout.completed).
    A terminal order is returned unchanged (idempotent)."""
    order = _get_order(order_id)
    if order.is_terminal():
        return order
    new_status = sandbox.next_status(order.status)
    order.status = new_status
    order.updated_at = _now()
    _orders[order_id] = order
    _emit(f"payout.{new_status}", order)
    return order
