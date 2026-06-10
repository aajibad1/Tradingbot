"""Standard API error model + status taxonomy (docs/06).

One error shape across every service so clients can parse failures uniformly and
every error is traceable back to a request and a business action:

    {
      "error": {
        "code": "route_unavailable",
        "message": "No route available for the requested corridor",
        "request_id": "req_123",
        "correlation_id": "corr_123"
      }
    }

Raise :class:`APIError` anywhere in a handler; the exception handlers installed by
``shared.http.install_contract`` render it into this envelope with the right HTTP
status and the request/correlation ids pulled from request state.
"""

from __future__ import annotations

from pydantic import BaseModel


class Status:
    """Canonical async-operation status taxonomy (docs/06).

    Funding, payout, settlement, onboarding, and route operations all report one of
    these so dashboards and webhooks share a single vocabulary."""

    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    ALL = frozenset({
        PENDING, QUEUED, PROCESSING, AWAITING_REVIEW,
        COMPLETED, PARTIALLY_COMPLETED, FAILED, CANCELLED,
    })

    TERMINAL = frozenset({COMPLETED, PARTIALLY_COMPLETED, FAILED, CANCELLED})


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    correlation_id: str | None = None


class ErrorModel(BaseModel):
    """The full error response body: ``{"error": {...}}``."""

    error: ErrorBody


class APIError(Exception):
    """A handled, client-facing error.

    ``code`` is a stable machine string (snake_case, e.g. ``route_unavailable``);
    ``message`` is human-readable; ``http_status`` is the response code. The
    request/correlation ids are filled in by the exception handler from request
    state, so callers never thread them through by hand."""

    def __init__(self, code: str, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def body(self, *, request_id: str | None = None, correlation_id: str | None = None) -> dict:
        return ErrorModel(
            error=ErrorBody(
                code=self.code, message=self.message,
                request_id=request_id, correlation_id=correlation_id,
            )
        ).model_dump()
