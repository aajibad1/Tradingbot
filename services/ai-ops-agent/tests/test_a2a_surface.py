"""ai-ops-agent A2A surface — invoke advertised ops tools; NEVER-tier stays hidden.

The CRITICAL invariant (NEVER-tier tools are neither discoverable nor invocable)
must hold over A2A exactly as it does over HTTP/MCP."""
from __future__ import annotations

import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def _invoke(data, req_id="1"):
    return client.post("/a2a", json={
        "jsonrpc": "2.0", "id": req_id, "method": "message/send",
        "params": {"message": {"kind": "message", "role": "user",
                               "parts": [{"kind": "data", "data": data}], "messageId": "m1"}},
    }).json()


def test_card_advertises_read_tools_but_never_tier_is_absent():
    card = client.get("/.well-known/agent-card.json").json()
    skill_ids = {s["id"] for s in card["skills"]}
    assert "get_balances" in skill_ids                       # READ tool advertised
    for never in ("withdraw_funds", "increase_leverage",
                  "add_exchange_key", "disable_kill_switch"):
        assert never not in skill_ids                        # NEVER tools hidden


def test_never_tier_tool_is_refused_without_acknowledgement():
    # withdraw_funds is NEVER-tier → not in _TOOL_IMPLS → same 'unknown tool'
    # response as a genuinely unknown name (no acknowledgement of its existence).
    r = _invoke({"tool": "withdraw_funds"})
    ghost = _invoke({"tool": "does_not_exist"}, req_id="2")
    # Indistinguishable failure mode: same code, same generic 'unknown tool'
    # template (only the caller's own name echoed) — no 'blocked'/'never' tell.
    assert r["error"]["code"] == ghost["error"]["code"] == -32602
    assert r["error"]["message"].startswith("unknown tool")
    assert "withdraw_funds" not in str(r["error"].get("data") or "")
    for tell in ("block", "never", "forbid", "denied"):
        assert tell not in r["error"]["message"].lower()


def test_invoke_read_tool_returns_data(monkeypatch):
    monkeypatch.setattr(main, "call_tool", lambda name, args: {"usd": 1000.0, "tool": name})
    r = _invoke({"tool": "get_balances", "args": {}}, req_id="3")
    parts = r["result"]["status"]["message"]["parts"]
    assert parts[0]["text"] == "ai-ops:get_balances ok"
    assert parts[1]["data"] == {"usd": 1000.0, "tool": "get_balances"}


def test_missing_tool_is_invalid_params():
    r = _invoke({}, req_id="4")
    assert r["error"]["code"] == -32602
