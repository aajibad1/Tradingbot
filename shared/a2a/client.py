"""A2A client — fetch another agent's card and call it over JSON-RPC.

For agent→agent calls. ``httpx`` is imported lazily so importing the contract
(models/server) stays dependency-light; the client is only constructed when an
agent actually needs to talk to a peer."""
from __future__ import annotations

import uuid

from .errors import A2AError
from .models import AgentCard, Message, Task, text_part
from .server import AGENT_CARD_PATH


class A2AClient:
    """Minimal JSON-RPC A2A client over HTTP."""

    def __init__(self, base_url: str, *, timeout: float = 30.0, transport=None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        # transport is an httpx.BaseTransport injection point — lets callers point
        # the client at an in-process ASGI/mock app in tests without a live server.
        self._transport = transport

    def _client(self):
        import httpx  # lazy: keep the contract importable without httpx
        return httpx.Client(timeout=self._timeout, transport=self._transport)

    def _post(self, method: str, params: dict) -> dict:
        payload = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
        with self._client() as http:
            resp = http.post(f"{self._base}/a2a", json=payload)
            resp.raise_for_status()
            body = resp.json()
        if body.get("error") is not None:
            err = body["error"]
            raise A2AError(err.get("code", -32603), err.get("message"), err.get("data"))
        return body.get("result", {})

    def fetch_card(self) -> AgentCard:
        with self._client() as http:
            resp = http.get(f"{self._base}{AGENT_CARD_PATH}")
            resp.raise_for_status()
            return AgentCard.model_validate(resp.json())

    def send_text(self, text: str, *, context_id: str | None = None) -> Task:
        """Send a one-shot text message and return the resulting Task."""
        msg = Message(role="user", parts=[text_part(text)],
                      message_id=uuid.uuid4().hex, context_id=context_id)
        result = self._post("message/send", {"message": msg.wire()})
        return Task.model_validate(result)

    def get_task(self, task_id: str) -> Task:
        return Task.model_validate(self._post("tasks/get", {"id": task_id}))

    def cancel_task(self, task_id: str) -> Task:
        return Task.model_validate(self._post("tasks/cancel", {"id": task_id}))
