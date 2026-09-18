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

Config (env):
  COMPLIANCE_SERVICE_URL — opt-in per-order: only consulted when the order was
  created with a screening_check_id (issue #22). Unlike this repo's other
  cross-service enrichments (fail-soft/advisory), this is a compliance gate and
  fails CLOSED — an escalated, still-pending, or unverifiable screening check
  routes the order to awaiting_review instead of provider submission.
  PAYMENT_APPROVAL_SERVICE_URL — opt-in per-order: only consulted when the
  order was created with an approval_request_id (issue #21). Same fail-closed
  posture as the compliance gate, independent of it — a pending, rejected,
  expired, or unverifiable approval request routes the order to
  awaiting_review instead of provider submission.

Order/idempotency state is in-memory (single-instance sandbox). Production would
back both with Redis (shared.http.RedisIdempotencyStore) — noted, not wired.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

import httpx
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
        screening_check_id=req.screening_check_id,
        approval_request_id=req.approval_request_id,
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


_GATED_STATUSES = frozenset({Status.PENDING, Status.AWAITING_REVIEW})


def _screening_is_clear(check_id: str) -> bool:
    """True only if compliance-service reports this screening check as
    verdict='clear'. Fails CLOSED on every other outcome — no
    COMPLIANCE_SERVICE_URL configured, the service unreachable, a non-200,
    a malformed body, or a verdict of 'pending'/'escalated' — because
    "couldn't verify" must never be treated as "clear" for a compliance gate
    (contrast with the opportunity-ranker/venue-anomaly-detector style
    enrichments elsewhere in this repo, which are advisory and fail-soft)."""
    base = os.environ.get("COMPLIANCE_SERVICE_URL")
    if not base:
        logger.warning("screening check %s requested but COMPLIANCE_SERVICE_URL "
                        "unset (fail-closed, blocking)", check_id)
        return False
    try:
        r = httpx.get(f"{base.rstrip('/')}/v1/screening/checks/{check_id}", timeout=3.0)
        r.raise_for_status()
        return r.json().get("verdict") == "clear"
    except Exception:  # noqa: BLE001 — any failure fails closed, never silently proceeds
        logger.warning("screening check %s unverifiable (fail-closed, blocking)", check_id)
        return False


def _approval_is_granted(request_id: str) -> bool:
    """True only if payment-approval-service reports this request as
    status='approved'. Fails CLOSED on every other outcome — no
    PAYMENT_APPROVAL_SERVICE_URL configured, the service unreachable, a
    non-200, a malformed body, or a status of 'pending'/'rejected'/'expired'
    (same posture as _screening_is_clear — see its docstring)."""
    base = os.environ.get("PAYMENT_APPROVAL_SERVICE_URL")
    if not base:
        logger.warning("approval request %s requested but PAYMENT_APPROVAL_SERVICE_URL "
                        "unset (fail-closed, blocking)", request_id)
        return False
    try:
        r = httpx.get(f"{base.rstrip('/')}/v1/approvals/{request_id}", timeout=3.0)
        r.raise_for_status()
        return r.json().get("status") == "approved"
    except Exception:  # noqa: BLE001 — any failure fails closed, never silently proceeds
        logger.warning("approval request %s unverifiable (fail-closed, blocking)", request_id)
        return False


@app.post("/v1/onramp/orders/{order_id}/advance", response_model=Order)
def advance_order(order_id: str) -> Order:
    """Advance the sandbox order one lifecycle step and emit the funding event.

    pending → [awaiting_review] → processing (funding.processing) → completed
    (funding.completed). A terminal order is returned unchanged (idempotent).

    Two independent fail-closed gates, both opt-in per-order and both must
    clear before the order may leave pending/awaiting_review:
      - Compliance gate (issue #22): screening_check_id must be verdict='clear'.
      - Approval gate (issue #21): approval_request_id must be status='approved'.
    Either one blocking (or unset URL, or unverifiable) routes the order to
    (or holds it at) awaiting_review instead of provider submission."""
    order = _get_order(order_id)
    if order.is_terminal():
        return order
    if order.status in _GATED_STATUSES:
        screening_blocked = (order.screening_check_id is not None
                              and not _screening_is_clear(order.screening_check_id))
        approval_blocked = (order.approval_request_id is not None
                             and not _approval_is_granted(order.approval_request_id))
        if screening_blocked or approval_blocked:
            if order.status != Status.AWAITING_REVIEW:
                order.status = Status.AWAITING_REVIEW
                order.updated_at = _now()
                _orders[order_id] = order
                _emit("funding.awaiting_review", order)
            return order
        # Both gates (whichever apply) are clear — proceed as the pending ->
        # processing step, regardless of whether we arrived here from pending
        # directly or recovered from a prior awaiting_review.
        order.status = Status.PENDING
    new_status = sandbox.next_status(order.status)
    order.status = new_status
    order.updated_at = _now()
    _orders[order_id] = order
    _emit(f"funding.{new_status}", order)
    return order
