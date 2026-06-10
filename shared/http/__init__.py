"""Shared HTTP API-contract layer (docs/06).

One import wires a FastAPI service into the platform contract:

    from shared.http import install_contract
    app = FastAPI(...)
    install_contract(app, service_name="onramp-orchestrator")

That installs:
  - correlation middleware (x-request-id / x-correlation-id propagate + echo), and
  - exception handlers that render APIError — and any uncaught error — into the
    standard ``{"error": {code, message, request_id, correlation_id}}`` body.

Idempotency is a building block (``IdempotencyStore`` + ``idempotency_key``) rather
than auto-wired, because key scoping is per-endpoint — see shared/http/idempotency.py.
"""

from __future__ import annotations

from .correlation import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    CorrelationMiddleware,
    get_correlation_id,
    get_request_id,
)
from .errors import APIError, ErrorBody, ErrorModel, Status
from .idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
    idempotency_key,
)

__all__ = [
    "install_contract",
    "APIError", "ErrorBody", "ErrorModel", "Status",
    "CorrelationMiddleware", "get_request_id", "get_correlation_id",
    "REQUEST_ID_HEADER", "CORRELATION_ID_HEADER",
    "IdempotencyStore", "InMemoryIdempotencyStore", "RedisIdempotencyStore",
    "idempotency_key", "IDEMPOTENCY_KEY_HEADER",
]


def install_contract(app, *, service_name: str):
    """Wire the API contract (correlation middleware + standard error handlers)
    onto a FastAPI app. Returns the app for chaining."""
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    app.add_middleware(CorrelationMiddleware)

    @app.exception_handler(APIError)
    async def _handle_api_error(request: Request, exc: APIError):  # noqa: ANN202
        return JSONResponse(
            exc.body(
                request_id=get_request_id(request),
                correlation_id=get_correlation_id(request),
            ),
            status_code=exc.http_status,
        )

    @app.exception_handler(Exception)
    async def _handle_uncaught(request: Request, exc: Exception):  # noqa: ANN202
        # Never leak internals; still traceable via the ids. The middleware has
        # already stamped request.state, so the client gets a correlatable 500.
        err = APIError("internal_error", "An unexpected error occurred.", http_status=500)
        return JSONResponse(
            err.body(
                request_id=get_request_id(request),
                correlation_id=get_correlation_id(request),
            ),
            status_code=500,
        )

    return app
