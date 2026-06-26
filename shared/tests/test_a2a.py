"""A2A (Agent2Agent) contract — models, JSON-RPC server, task lifecycle, errors.

Covered by .claude/verify (shared/tests). Exercises the wire surface every agent
service shares, so a regression here is caught once rather than per-service."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.a2a import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    InMemoryTaskStore,
    Message,
    TaskState,
    data_part,
    errors,
    install_a2a,
    text_part,
)


def _app(handler):
    app = FastAPI()
    card = AgentCard(
        name="test-agent", description="d", url="http://localhost:9/a2a", version="0.1.0",
        capabilities=AgentCapabilities(),
        skills=[AgentSkill(id="echo", name="Echo", description="echoes", tags=["t"])],
    )
    install_a2a(app, card=card, handler=handler)
    return TestClient(app)


def _send(client, text="hi", req_id="1"):
    return client.post("/a2a", json={
        "jsonrpc": "2.0", "id": req_id, "method": "message/send",
        "params": {"message": {"kind": "message", "role": "user",
                               "parts": [{"kind": "text", "text": text}], "messageId": "m1"}},
    }).json()


# --- models -------------------------------------------------------------------
def test_message_wire_is_camelcase_and_roundtrips():
    m = Message(role="user", parts=[text_part("hello")], message_id="m1", context_id="c1")
    wire = m.wire()
    assert wire["messageId"] == "m1" and wire["contextId"] == "c1"
    assert "task_id" not in wire and "taskId" not in wire  # unset optionals dropped
    back = Message.model_validate(wire)
    assert back.message_id == "m1" and back.text() == "hello"


def test_message_accepts_snake_or_camel_on_input():
    snake = Message.model_validate({"role": "agent", "parts": [{"kind": "text", "text": "x"}],
                                    "message_id": "m2"})
    assert snake.message_id == "m2"


def test_agent_card_defaults():
    card = AgentCard(name="n", description="d", url="u", version="1.0")
    w = card.wire()
    assert w["protocolVersion"] == "0.3.0" and w["preferredTransport"] == "JSONRPC"
    assert w["defaultInputModes"] == ["text/plain"]
    assert w["capabilities"] == {"streaming": False, "pushNotifications": False,
                                 "stateTransitionHistory": False}


# --- discovery + message/send -------------------------------------------------
def test_agent_card_served_at_well_known():
    c = _app(lambda m: [text_part("ok")])
    card = c.get("/.well-known/agent-card.json").json()
    assert card["name"] == "test-agent" and card["skills"][0]["id"] == "echo"


def test_message_send_wraps_parts_in_completed_task():
    c = _app(lambda m: [text_part(f"echo: {m.text()}")])
    r = _send(c, "hi", req_id="42")
    assert r["jsonrpc"] == "2.0" and r["id"] == "42"
    task = r["result"]
    assert task["kind"] == "task" and task["status"]["state"] == TaskState.COMPLETED
    assert task["status"]["message"]["role"] == "agent"
    assert task["status"]["message"]["parts"][0]["text"] == "echo: hi"
    assert task["artifacts"][0]["parts"][0]["text"] == "echo: hi"
    assert len(task["history"]) == 2  # incoming + reply
    assert task["history"][0]["taskId"] == task["id"]  # stamped with the new task id


def test_handler_can_return_data_part():
    c = _app(lambda m: [data_part({"score": 0.9, "label": "ok"})])
    task = _send(c)["result"]
    assert task["status"]["message"]["parts"][0]["data"] == {"score": 0.9, "label": "ok"}


def test_handler_returning_message_is_stateless():
    reply = Message(role="agent", parts=[text_part("direct")], message_id="r1")
    c = _app(lambda m: reply)
    out = _send(c)["result"]
    assert out["kind"] == "message" and out["parts"][0]["text"] == "direct"


# --- task lifecycle -----------------------------------------------------------
def test_tasks_get_roundtrip_and_not_found():
    c = _app(lambda m: [text_part("ok")])
    tid = _send(c)["result"]["id"]
    got = c.post("/a2a", json={"jsonrpc": "2.0", "id": 2, "method": "tasks/get",
                               "params": {"id": tid}}).json()
    assert got["result"]["id"] == tid
    miss = c.post("/a2a", json={"jsonrpc": "2.0", "id": 3, "method": "tasks/get",
                                "params": {"id": "ghost"}}).json()
    assert miss["error"]["code"] == errors.TASK_NOT_FOUND


def test_completed_task_is_not_cancelable():
    c = _app(lambda m: [text_part("ok")])
    tid = _send(c)["result"]["id"]
    r = c.post("/a2a", json={"jsonrpc": "2.0", "id": 4, "method": "tasks/cancel",
                             "params": {"id": tid}}).json()
    assert r["error"]["code"] == errors.TASK_NOT_CANCELABLE


def test_cancel_working_task_transitions_to_canceled():
    store = InMemoryTaskStore()
    from shared.a2a.models import Task as T
    from shared.a2a.models import TaskStatus
    store.put(T(id="t1", context_id="c1", status=TaskStatus(state=TaskState.WORKING)))
    app = FastAPI()
    card = AgentCard(name="n", description="d", url="u", version="1.0")
    install_a2a(app, card=card, handler=lambda m: [text_part("x")], store=store)
    c = TestClient(app)
    r = c.post("/a2a", json={"jsonrpc": "2.0", "id": 5, "method": "tasks/cancel",
                             "params": {"id": "t1"}}).json()
    assert r["result"]["status"]["state"] == TaskState.CANCELED


# --- error taxonomy -----------------------------------------------------------
@pytest.mark.parametrize("method,params,code", [
    ("bogus/method", {}, errors.METHOD_NOT_FOUND),
    ("message/stream", {}, errors.UNSUPPORTED_OPERATION),
    ("tasks/pushNotificationConfig/set", {}, errors.UNSUPPORTED_OPERATION),
    ("message/send", {}, errors.INVALID_PARAMS),
])
def test_error_codes(method, params, code):
    c = _app(lambda m: [text_part("ok")])
    r = c.post("/a2a", json={"jsonrpc": "2.0", "id": 9, "method": method, "params": params}).json()
    assert r["error"]["code"] == code


def test_bad_envelope_is_invalid_request():
    c = _app(lambda m: [text_part("ok")])
    r = c.post("/a2a", json={"jsonrpc": "1.0", "method": "x"}).json()
    assert r["error"]["code"] == errors.INVALID_REQUEST


def test_malformed_json_is_parse_error():
    c = _app(lambda m: [text_part("ok")])
    r = c.post("/a2a", content=b"{not json", headers={"content-type": "application/json"}).json()
    assert r["error"]["code"] == errors.PARSE_ERROR


def test_handler_crash_fails_soft_to_internal_error():
    def boom(m):
        raise RuntimeError("kaboom")
    c = _app(boom)
    r = _send(c)
    assert r["error"]["code"] == errors.INTERNAL_ERROR
    assert "kaboom" not in r["error"]["message"]  # internals not leaked


def test_handler_can_raise_typed_a2a_error():
    from shared.a2a import A2AError

    def picky(m):
        raise A2AError(errors.CONTENT_TYPE_NOT_SUPPORTED)
    c = _app(picky)
    assert _send(c)["error"]["code"] == errors.CONTENT_TYPE_NOT_SUPPORTED


# --- store --------------------------------------------------------------------
def test_store_evicts_oldest_over_cap():
    from shared.a2a.models import Task as T
    from shared.a2a.models import TaskStatus
    store = InMemoryTaskStore(max_tasks=2)
    for i in range(3):
        store.put(T(id=f"t{i}", context_id="c", status=TaskStatus(state=TaskState.COMPLETED)))
    assert store.get("t0") is None  # evicted
    assert store.get("t2") is not None and store.get("t1") is not None
