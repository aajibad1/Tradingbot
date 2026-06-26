"""approval-gate-service A2A surface — propose an action, get a permission decision."""
from __future__ import annotations

import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def _send(data, req_id="1"):
    return client.post("/a2a", json={
        "jsonrpc": "2.0", "id": req_id, "method": "message/send",
        "params": {"message": {"kind": "message", "role": "user",
                               "parts": [{"kind": "data", "data": data}], "messageId": "m1"}},
    }).json()


def test_agent_card():
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "approval-gate-service"
    assert any(s["id"] == "evaluate-action" for s in card["skills"])


def test_read_action_auto_approved():
    r = _send({"agent": "opportunity-ranker", "action_type": "read", "summary": "read funding"})
    p = r["result"]["status"]["message"]["parts"][1]["data"]
    assert p["classification"] == "auto_approved" and p["decided_by"] == "policy"


def test_withdrawal_is_blocked():
    r = _send({"agent": "x", "action_type": "withdrawal", "summary": "pull funds"}, req_id="2")
    p = r["result"]["status"]["message"]["parts"][1]["data"]
    assert p["classification"] == "blocked"


def test_missing_data_is_invalid_params():
    r = client.post("/a2a", json={
        "jsonrpc": "2.0", "id": "3", "method": "message/send",
        "params": {"message": {"kind": "message", "role": "user",
                               "parts": [{"kind": "text", "text": "hi"}], "messageId": "m3"}},
    }).json()
    assert r["error"]["code"] == -32602
