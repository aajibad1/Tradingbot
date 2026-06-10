"""onramp-orchestrator — fiat → stablecoin on-ramp API (docs/05 API plane, docs/06).

SANDBOX-ONLY. No real money moves: the only provider is the deterministic
sandbox simulator (sandbox_provider.py), and real rails are gated on money-
transmission licensing (docs/REGULATORY_BRIEF.md). This is the doc-sanctioned
sandbox-first build (docs/06 "sandbox tier", docs/12 onboarding).

It exercises the full platform contract:
  - shared.http.install_contract — x-request-id / x-correlation-id + error model
  - Idempotency-Key on POST /orders (a retried create never makes two orders)
  - shared.http.Status taxonomy for order lifecycle
  - canonical event envelope — funding.* lifecycle events via publish_event

Endpoints (docs/06):
  GET  /healthz
  POST /v1/onramp/quotes
  POST /v1/onramp/orders                 (idempotent via Idempotency-Key)
  GET  /v1/onramp/orders/{id}
  POST /v1/onramp/orders/{id}/advance    (sandbox lifecycle step; cron-ping)

Order/idempotency state is in-memory (single-instance sandbox). Production would
back both with Redis (shared.http.RedisIdempotencyStore) — noted, not wired.
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
logger = logging.getLogger("onramp-orchestrator")

PRODUCER = "onramp-orchestrator"
QUOTE_TTL_SECONDS = 120

app = FastAPI(title="onramp-orchestrator", version="0.1.0")
install_contract(app, service_name=PRODUCER)

# In-memory state (single-instance sandbox). Prod: Redis-backed.
_orders: dict[str, Order] = {}
_idempotency = InMemoryIdempotencyStore()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _emit(event_type: str, order: Order) -> None:
    """Emit a funding lifecycle event wrapped in the canonical envelope (fail-open
    — instrumentation must never break the request path)."""
    try:
        get_publisher().publish_event(
            Topic.FUNDING_EVENTS, event_type, order,
            producer=PRODUCER, tenant_id=order.tenant_id,
            correlation_id=order.correlation_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning("funding event %s emit failed for order=%s (non-fatal)", event_type, order.id)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": "sandbox"}


@app.post("/v1/onramp/quotes", response_model=Quote)
def create_quote(req: QuoteRequest) -> Quote:
    fee, rate, dest_amount = sandbox.quote(req.source_currency, req.dest_asset, req.amount)
    return Quote(
        quote_id=_new_id("qt"),
        source_currency=req.source_currency,
        dest_asset=req.dest_asset,
        amount=req.amount,
        fee=fee,
        rate=rate,
        dest_amount=dest_amount,
        expires_at=_now(),
    )


@app.post("/v1/onramp/orders", response_model=Order)
def create_order(req: OrderRequest, request: Request) -> Order:
    # Idempotency (docs/06): a retried create with the same Idempotency-Key returns
    # the original order instead of opening a second one.
    key = idempotency_key(request)
    if key is not None:
        cached = _idempotency.get(key)
        if cached is not None:
            return Order.model_validate(cached)

    fee, _rate, dest_amount = sandbox.quote(req.source_currency, req.dest_asset, req.amount)
    now = _now()
    order = Order(
        id=_new_id("ord"),
        status=Status.PENDING,
        source_currency=req.source_currency,
        dest_asset=req.dest_asset,
        amount=req.amount,
        fee=fee,
        dest_amount=dest_amount,
        destination_wallet=req.destination_wallet,
        tenant_id=request.headers.get("x-tenant-id"),
        correlation_id=get_correlation_id(request) or _new_id("corr"),
        created_at=now,
        updated_at=now,
    )
    _orders[order.id] = order
    if key is not None:
        _idempotency.put(key, order.model_dump(mode="json"))
    _emit("funding.created", order)
    logger.info("onramp order created id=%s %s %.2f%s→%s", order.id, req.source_currency,
                req.amount, req.source_currency, req.dest_asset)
    return order


def _get_order(order_id: str) -> Order:
    order = _orders.get(order_id)
    if order is None:
        raise APIError("order_not_found", f"order {order_id} not found", http_status=404)
    return order


@app.get("/v1/onramp/orders/{order_id}", response_model=Order)
def get_order(order_id: str) -> Order:
    return _get_order(order_id)


@app.post("/v1/onramp/orders/{order_id}/advance", response_model=Order)
def advance_order(order_id: str) -> Order:
    """Advance the sandbox order one lifecycle step and emit the funding event.

    pending → processing (funding.processing) → completed (funding.completed).
    A terminal order is returned unchanged (idempotent)."""
    order = _get_order(order_id)
    if order.is_terminal():
        return order
    new_status = sandbox.next_status(order.status)
    order.status = new_status
    order.updated_at = _now()
    _orders[order_id] = order
    _emit(f"funding.{new_status}", order)
    return order
