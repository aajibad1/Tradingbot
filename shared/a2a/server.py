"""FastAPI integration — one call wires an agent into A2A.

    from shared.a2a import install_a2a, AgentCard, AgentSkill, text_part

    def handle(message):              # message/send handler
        return [text_part(answer(message.text()))]

    install_a2a(app, card=AgentCard(...), handler=handle)

Mounts ``GET /.well-known/agent-card.json`` (discovery) and ``POST /a2a``
(JSON-RPC 2.0: ``message/send``, ``tasks/get``, ``tasks/cancel``). The handler
returns the agent's reply parts (wrapped into a completed Task), or a Message
(stateless reply), or a fully-formed Task. Any unexpected error degrades to a
JSON-RPC internal error — a misbehaving agent never 500s its caller."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable, Union

from starlette.requests import Request
from starlette.responses import JSONResponse

from . import errors
from .models import (
    AgentCard,
    Artifact,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    rpc_error,
    rpc_result,
)
from .store import InMemoryTaskStore

logger = logging.getLogger("a2a.server")

HandlerResult = Union[list[Part], Message, Task]
Handler = Callable[[Message], HandlerResult]

AGENT_CARD_PATH = "/.well-known/agent-card.json"
_STREAMING_METHODS = frozenset({"message/stream", "tasks/resubscribe"})
_PUSH_METHODS = frozenset({
    "tasks/pushNotificationConfig/set", "tasks/pushNotificationConfig/get",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def install_a2a(app, *, card: AgentCard, handler: Handler,
                store: InMemoryTaskStore | None = None,
                path: str = "/a2a",
                agent_card_path: str = AGENT_CARD_PATH):
    """Wire an A2A Agent Card + JSON-RPC endpoint onto a FastAPI app. Returns app."""
    task_store = store or InMemoryTaskStore()

    @app.get(agent_card_path)
    async def _agent_card() -> dict:  # noqa: ANN202
        return card.wire()

    @app.post(path)
    async def _rpc(request: Request):  # noqa: ANN202
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — malformed JSON is a protocol error, not a 500
            return JSONResponse(rpc_error(None, errors.PARSE_ERROR, "Invalid JSON payload"))

        req_id = body.get("id") if isinstance(body, dict) else None
        method = body.get("method") if isinstance(body, dict) else None
        params = body.get("params") if isinstance(body, dict) else None
        if body.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return JSONResponse(
                rpc_error(req_id, errors.INVALID_REQUEST, "Invalid JSON-RPC request"))

        try:
            result = _dispatch(method, params or {}, card, handler, task_store)
            return JSONResponse(rpc_result(req_id, result))
        except errors.A2AError as exc:
            return JSONResponse(rpc_error(req_id, exc.code, exc.message, exc.data))
        except Exception:  # noqa: BLE001 — fail-soft: never leak a 500 to the caller
            logger.exception("a2a handler crashed (method=%s)", method)
            return JSONResponse(rpc_error(req_id, errors.INTERNAL_ERROR, "Internal error"))

    return app


def _dispatch(method: str, params: dict, card: AgentCard, handler: Handler,
              store: InMemoryTaskStore) -> dict:
    if method == "message/send":
        return _message_send(params, handler, store)
    if method == "tasks/get":
        return _tasks_get(params, store)
    if method == "tasks/cancel":
        return _tasks_cancel(params, store)
    if method in _STREAMING_METHODS or method in _PUSH_METHODS:
        # capabilities advertise streaming/push = false → explicitly unsupported.
        raise errors.A2AError(errors.UNSUPPORTED_OPERATION)
    raise errors.A2AError(errors.METHOD_NOT_FOUND, f"Method not found: {method}")


def _message_send(params: dict, handler: Handler, store: InMemoryTaskStore) -> dict:
    raw = params.get("message")
    if not isinstance(raw, dict):
        raise errors.A2AError(errors.INVALID_PARAMS, "params.message is required")
    try:
        incoming = Message.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — bad message shape is invalid params
        raise errors.A2AError(errors.INVALID_PARAMS, f"Invalid message: {exc}") from exc

    context_id = incoming.context_id or _new_id("ctx")
    result = handler(incoming)

    # A handler may answer with a Message (stateless), a Task (full control), or
    # just its reply parts (the common case → wrap in a completed Task).
    if isinstance(result, Task):
        store.put(result)
        return result.wire()
    if isinstance(result, Message):
        if not result.message_id:
            result.message_id = _new_id("msg")
        return result.wire()

    parts = list(result)
    task_id = _new_id("task")
    reply = Message(role="agent", parts=parts, message_id=_new_id("msg"),
                    task_id=task_id, context_id=context_id)
    incoming.task_id = task_id
    incoming.context_id = context_id
    task = Task(
        id=task_id, context_id=context_id,
        status=TaskStatus(state=TaskState.COMPLETED, message=reply, timestamp=_now()),
        history=[incoming, reply],
        artifacts=[Artifact(artifact_id=_new_id("artifact"), name="result", parts=parts)],
    )
    store.put(task)
    return task.wire()


def _tasks_get(params: dict, store: InMemoryTaskStore) -> dict:
    task = store.get(params.get("id", ""))
    if task is None:
        raise errors.A2AError(errors.TASK_NOT_FOUND)
    return task.wire()


def _tasks_cancel(params: dict, store: InMemoryTaskStore) -> dict:
    task = store.get(params.get("id", ""))
    if task is None:
        raise errors.A2AError(errors.TASK_NOT_FOUND)
    if task.status.state in TaskState.TERMINAL:
        raise errors.A2AError(errors.TASK_NOT_CANCELABLE)
    task.status = TaskStatus(state=TaskState.CANCELED, timestamp=_now())
    store.put(task)
    return task.wire()
