"""A2A discovery + client — resolve peers by name, call them over JSON-RPC.

The client is exercised against an in-process app (ASGI transport), so the
round-trip — build request → server dispatch → parse Task/error — is covered
without a live server."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.a2a import (
    A2AClient,
    A2AError,
    AgentCard,
    AgentSkill,
    base_url,
    client_for,
    data_part,
    errors,
    install_a2a,
    text_part,
)


# --- registry / discovery -----------------------------------------------------
def test_base_url_defaults_to_local_port():
    assert base_url("debate-service") == "http://localhost:8340"


def test_base_url_env_override(monkeypatch):
    monkeypatch.setenv("A2A_DEBATE_SERVICE_URL", "https://debate.prod.example/")
    assert base_url("debate-service") == "https://debate.prod.example"  # trailing / stripped


def test_base_url_unknown_agent_fails_loud():
    with pytest.raises(KeyError):
        base_url("no-such-agent")


def test_client_for_targets_named_agent():
    c = client_for("agent-evals")
    assert isinstance(c, A2AClient) and c._base == "http://localhost:8343"


# --- client ↔ server round-trip (ASGI transport) ------------------------------
def _peer_client():
    app = FastAPI()
    card = AgentCard(name="peer", description="d", url="http://peer/a2a", version="1.0",
                     skills=[AgentSkill(id="echo", name="Echo", description="echoes")])

    def handle(msg):
        if msg.text() == "boom":
            raise A2AError(errors.CONTENT_TYPE_NOT_SUPPORTED)
        return [text_part(f"echo: {msg.text()}"), data_part({"len": len(msg.text())})]

    install_a2a(app, card=card, handler=handle)
    # Sync httpx.Client can't drive ASGITransport (async-only); delegate through a
    # MockTransport backed by TestClient so the client↔server round-trip is real.
    tc = TestClient(app)

    def _delegate(request: httpx.Request) -> httpx.Response:
        r = tc.request(request.method, request.url.path,
                       content=request.content, headers=request.headers)
        return httpx.Response(r.status_code, content=r.content,
                              headers={"content-type": r.headers.get("content-type",
                                                                     "application/json")})

    return A2AClient("http://peer", transport=httpx.MockTransport(_delegate))


def test_client_fetch_card():
    card = _peer_client().fetch_card()
    assert card.name == "peer" and card.skills[0].id == "echo"


def test_client_send_text_returns_task():
    task = _peer_client().send_text("hello")
    assert task.status.state == "completed"
    assert task.status.message.text() == "echo: hello"
    assert task.artifacts[0].parts[1].data == {"len": 5}


def test_client_raises_typed_error_from_peer():
    with pytest.raises(A2AError) as ei:
        _peer_client().send_text("boom")
    assert ei.value.code == errors.CONTENT_TYPE_NOT_SUPPORTED


def test_client_get_and_cancel_task():
    client = _peer_client()
    tid = client.send_text("hi").id
    assert client.get_task(tid).id == tid
    with pytest.raises(A2AError) as ei:
        client.cancel_task(tid)  # completed → not cancelable
    assert ei.value.code == errors.TASK_NOT_CANCELABLE
