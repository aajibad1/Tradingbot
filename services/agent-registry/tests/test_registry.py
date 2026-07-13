"""agent-registry: agents, immutable prompt versions, eval-gated activation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from shared.a2a import AgentCard, AgentSkill, Message, Task, TaskStatus, data_part

import main


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("AGENT_EVALS_URL", raising=False)
    monkeypatch.delenv("A2A_AGENT_EVALS_URL", raising=False)
    main._agents.clear()
    return TestClient(main.app)


def _stub_evals(monkeypatch, verdict: dict):
    """Stand in for the agent-evals A2A peer: _eval_passed calls
    A2AClient(base).send_data(...) → a completed Task carrying the verdict.
    `verdict` is read at call time, so mutating it flips the answer mid-test."""
    class _Stub:
        def __init__(self, base, timeout=None):
            pass

        def send_data(self, data):
            reply = Message(role="agent", parts=[data_part(verdict)], message_id="r")
            return Task(id="t", context_id="c",
                        status=TaskStatus(state="completed", message=reply))

    monkeypatch.setattr(main, "A2AClient", _Stub)


def _agent(client, name="ranker"):
    return client.post("/v1/agents", json={"name": name, "provider": "claude"}).json()


def test_register_and_get(client):
    _agent(client)
    a = client.get("/v1/agents/ranker").json()
    assert a["name"] == "ranker" and a["active"] is None


def test_duplicate_agent_409(client):
    _agent(client)
    assert client.post("/v1/agents", json={"name": "ranker"}).status_code == 409


def test_prompt_versions_are_immutable(client):
    _agent(client)
    r = client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "score the opp"})
    assert r.json()["content_hash"]  # hashed, content not stored
    dup = client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "different"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "version_exists"


def _evals_card():
    return AgentCard(name="agent-evals", description="d", url="http://evals/a2a",
                     version="1.0",
                     skills=[AgentSkill(id="eval-verdict", name="E", description="d")])


def test_activation_allowed_without_evals_configured(client, monkeypatch):
    # TRUE sandbox: nothing on the roster answers discovery (no real network here —
    # unstubbed discovery would dial live local agents and flake).
    monkeypatch.setattr(main, "discover_agents", lambda **k: {})
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 200 and r.json()["active"] == "v1"


def test_activate_unknown_version_404(client):
    _agent(client)
    r = client.post("/v1/agents/ranker/activate", json={"version": "nope"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "version_not_found"


def test_activation_gated_on_eval_verdict_over_a2a(client, monkeypatch):
    monkeypatch.setenv("AGENT_EVALS_URL", "http://agent-evals")  # any base → evals wired
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})

    verdict = {"passed": False, "failures": ["accuracy 0.5 < 0.8"]}
    _stub_evals(monkeypatch, verdict)

    # failing eval → activation blocked
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "eval_not_passed"

    # passing eval → activation allowed
    verdict["passed"] = True
    r2 = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r2.status_code == 200 and r2.json()["active"] == "v1"


def test_activation_blocked_when_not_evaluated_over_a2a(client, monkeypatch):
    monkeypatch.setenv("A2A_AGENT_EVALS_URL", "http://agent-evals/a2a")
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})
    _stub_evals(monkeypatch, {"passed": False, "failures": ["not evaluated"]})
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 409 and "not evaluated" in r.json()["error"]["message"]


def test_activation_fail_closed_when_evals_unreachable(client, monkeypatch):
    monkeypatch.setenv("AGENT_EVALS_URL", "http://agent-evals")
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})

    class _Down:
        def __init__(self, base, timeout=None):
            pass

        def send_data(self, data):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(main, "A2AClient", _Down)
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 409 and "unreachable" in r.json()["error"]["message"]


def test_eval_peer_discovered_by_skill_when_env_unset(client, monkeypatch):
    # No explicit env (client fixture cleared both) → the registry must DISCOVER the
    # eval authority by capability and still gate on its verdict.
    monkeypatch.setattr(main, "discover_agents", lambda **k: {"agent-evals": _evals_card()})
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})
    _stub_evals(monkeypatch, {"passed": True})
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 200 and r.json()["active"] == "v1"


def test_mesh_reachable_but_no_eval_authority_fails_closed(client, monkeypatch):
    # The mesh IS running (debate answered) but nobody advertises eval-verdict —
    # e.g. agent-evals is down. This must NOT fall through to sandbox-allow: an
    # evals outage must never become an activation bypass.
    card = AgentCard(name="debate-service", description="d", url="http://debate/a2a",
                     version="1.0",
                     skills=[AgentSkill(id="verify-claim", name="V", description="d")])
    monkeypatch.setattr(main, "discover_agents", lambda **k: {"debate-service": card})
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 409
    assert "fail closed" in r.json()["error"]["message"]


def test_explicit_env_skips_discovery(client, monkeypatch):
    # An explicit env must WIN — discovery is never consulted (pinning / back-compat).
    monkeypatch.setenv("A2A_AGENT_EVALS_URL", "http://agent-evals/a2a")

    def _boom(*a, **k):
        raise AssertionError("discovery must not run when env is set")

    monkeypatch.setattr(main, "discover_agents", _boom)
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})
    _stub_evals(monkeypatch, {"passed": True})
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 200 and r.json()["active"] == "v1"
