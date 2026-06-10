"""Request correlation middleware (docs/06).

Every request carries an ``x-request-id`` (unique per HTTP request) and an
``x-correlation-id`` (stable across the whole business action — it threads the
API edge to the event bus, matching the ``correlation_id`` on the doc-07 event
envelope). Both are read from inbound headers when present (so a caller or an
upstream service can propagate them) and generated otherwise, stashed on
``request.state``, and echoed back on the response headers.

Adopt via ``shared.http.install_contract(app, service_name=...)``.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "x-request-id"
CORRELATION_ID_HEADER = "x-correlation-id"

_REQUEST_ID_ATTR = "request_id"
_CORRELATION_ID_ATTR = "correlation_id"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Assign/propagate request + correlation ids and echo them on the response."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or _new_id("req")
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or _new_id("corr")
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        response: Response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, _REQUEST_ID_ATTR, None)


def get_correlation_id(request: Request) -> str | None:
    return getattr(request.state, _CORRELATION_ID_ATTR, None)
