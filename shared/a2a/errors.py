"""A2A / JSON-RPC error taxonomy.

Standard JSON-RPC 2.0 codes plus the A2A-specific range (-32001..-32006). Raise
:class:`A2AError` inside a handler to return a structured JSON-RPC error; the
server renders it into the ``{jsonrpc, id, error:{code, message, data?}}`` body."""
from __future__ import annotations

from typing import Any

# Standard JSON-RPC 2.0
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# A2A-specific
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
PUSH_NOTIFICATION_NOT_SUPPORTED = -32003
UNSUPPORTED_OPERATION = -32004
CONTENT_TYPE_NOT_SUPPORTED = -32005
INVALID_AGENT_RESPONSE = -32006

_DEFAULT_MESSAGE = {
    PARSE_ERROR: "Invalid JSON payload",
    INVALID_REQUEST: "Request payload validation error",
    METHOD_NOT_FOUND: "Method not found",
    INVALID_PARAMS: "Invalid parameters",
    INTERNAL_ERROR: "Internal error",
    TASK_NOT_FOUND: "Task not found",
    TASK_NOT_CANCELABLE: "Task cannot be canceled",
    PUSH_NOTIFICATION_NOT_SUPPORTED: "Push Notification is not supported",
    UNSUPPORTED_OPERATION: "This operation is not supported",
    CONTENT_TYPE_NOT_SUPPORTED: "Incompatible content types",
    INVALID_AGENT_RESPONSE: "Invalid agent response",
}


class A2AError(Exception):
    """A JSON-RPC error a handler can raise to short-circuit with a code."""

    def __init__(self, code: int, message: str | None = None, data: Any = None) -> None:
        self.code = code
        self.message = message or _DEFAULT_MESSAGE.get(code, "Error")
        self.data = data
        super().__init__(self.message)
