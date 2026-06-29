"""agent-registry A2A surface — resolve an agent's active version."""
from __future__ import annotations

import main
from fastapi.testclient import TestClient
from shared.a2a import A2A_AGENTS, AgentCard, AgentSkill

client = TestClient(main.app)


def _resolve(data, req_id="1"):
    return client.post("/a2a", json={
        "jsonrpc": "2.0", "id": req_id, "method": "message/send",
        "params": {"message": {"kind": "message", "role": "user",
                               "parts": [{"kind": "data", "data": data}], "messageId": "m1"}},
    }).json()


def test_agent_card():
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "agent-registry"
    assert any(s["id"] == "resolve-active-version" for s in card["skills"])


def test_resolve_active_version_after_create_and_activate():
    client.post("/v1/agents", json={"name": "ranker-a2a", "role": "ranker", "model": "claude-opus-4-8"})
    client.post("/v1/agents/ranker-a2a/prompts", json={"version": "v1", "content": "rank well"})
    client.post("/v1/agents/ranker-a2a/activate", json={"version": "v1"})  # no AGENT_EVALS_URL → sandbox-allow
    r = _resolve({"agent": "ranker-a2a"})
    data = r["result"]["status"]["message"]["parts"][1]["data"]
    assert data["active"] == "v1" and "v1" in data["prompt_versions"]


def test_resolve_via_plain_text_name():
    r = client.post("/a2a", json={
        "jsonrpc": "2.0", "id": "2", "method": "message/send",
        "params": {"message": {"kind": "message", "role": "user",
                               "parts": [{"kind": "text", "text": "ranker-a2a"}], "messageId": "m2"}},
    }).json()
    assert r["result"]["status"]["message"]["parts"][1]["data"]["agent"] == "ranker-a2a"


def test_unknown_agent_is_invalid_params():
    r = _resolve({"agent": "ghost-agent"}, req_id="3")
    assert r["error"]["code"] == -32602


# --- A2A roster catalog -------------------------------------------------------
def test_catalog_lists_unreachable_when_no_peers_up(monkeypatch):
    # No agents are actually serving in-process → best-effort discovery yields an
    # empty catalog, and every roster member is reported unreachable (an ops view).
    monkeypatch.setattr(main, "discover_agents", lambda **k: {})
    body = client.get("/v1/a2a/catalog").json()
    assert body["count"] == 0 and body["agents"] == []
    assert body["roster"] == sorted(A2A_AGENTS)
    assert body["unreachable"] == sorted(A2A_AGENTS)


def test_catalog_projects_discovered_cards(monkeypatch):
    card = AgentCard(name="debate-service", description="adversarial verification",
                     url="http://debate/a2a", version="1.0",
                     skills=[AgentSkill(id="verify-claim", name="Verify", description="d")])
    monkeypatch.setattr(main, "discover_agents", lambda **k: {"debate-service": card})
    body = client.get("/v1/a2a/catalog").json()
    assert body["count"] == 1
    entry = body["agents"][0]
    assert entry["name"] == "debate-service" and entry["url"] == "http://debate/a2a"
    assert entry["skills"][0]["id"] == "verify-claim"
    assert "debate-service" not in body["unreachable"]  # reachable → not listed down
