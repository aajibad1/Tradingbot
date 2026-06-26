"""debate-service A2A surface — agent card + message/send produce a verdict.

No ANTHROPIC_API_KEY in the test env, so the panel abstains (verdict 'support'
at proposer confidence) — but the A2A envelope, task wrapping, and structured
verdict payload are exactly what a peer agent receives."""
from __future__ import annotations

import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_agent_card_advertises_verify_skill():
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "debate-service"
    assert card["protocolVersion"] == "0.3.0"
    assert any(s["id"] == "verify-claim" for s in card["skills"])
    assert card["url"].endswith("/a2a")


def test_message_send_returns_verdict_task(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/a2a", json={
        "jsonrpc": "2.0", "id": "1", "method": "message/send",
        "params": {"message": {"kind": "message", "role": "user",
                               "parts": [{"kind": "text", "text": "funding carry is real"}],
                               "messageId": "m1"}},
    }).json()
    task = r["result"]
    assert task["status"]["state"] == "completed"
    parts = task["status"]["message"]["parts"]
    # text summary + structured verdict data part
    assert parts[0]["text"].split()[0] in {"support", "reject", "uncertain"}
    verdict = parts[1]["data"]
    assert verdict["decision"] in {"support", "reject", "uncertain"}
    assert verdict["n_skeptics"] == 3 and len(verdict["transcript"]) == 4


def test_empty_claim_is_invalid_params():
    r = client.post("/a2a", json={
        "jsonrpc": "2.0", "id": "2", "method": "message/send",
        "params": {"message": {"kind": "message", "role": "user",
                               "parts": [{"kind": "text", "text": "   "}], "messageId": "m2"}},
    }).json()
    assert r["error"]["code"] == -32602
