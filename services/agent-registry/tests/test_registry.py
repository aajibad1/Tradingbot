"""agent-registry: agents, immutable prompt versions, eval-gated activation."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("AGENT_EVALS_URL", raising=False)
    main._agents.clear()
    return TestClient(main.app)


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


def test_activation_allowed_without_evals_configured(client):
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 200 and r.json()["active"] == "v1"


def test_activate_unknown_version_404(client):
    _agent(client)
    r = client.post("/v1/agents/ranker/activate", json={"version": "nope"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "version_not_found"


def test_activation_gated_on_eval_verdict(client, monkeypatch):
    monkeypatch.setenv("AGENT_EVALS_URL", "http://agent-evals")
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})

    verdict = {"passed": False, "failures": ["accuracy 0.5 < 0.8"]}

    def fake_get(url, params=None, timeout=None):
        class _R:
            status_code = 200
            def json(self_):  # noqa: N805
                return verdict
        return _R()

    monkeypatch.setattr(httpx, "get", fake_get)
    # failing eval → activation blocked
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "eval_not_passed"

    # passing eval → activation allowed
    verdict["passed"] = True
    r2 = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r2.status_code == 200 and r2.json()["active"] == "v1"


def test_activation_blocked_when_not_evaluated(client, monkeypatch):
    monkeypatch.setenv("AGENT_EVALS_URL", "http://agent-evals")
    _agent(client)
    client.post("/v1/agents/ranker/prompts", json={"version": "v1", "content": "x"})

    def fake_get(url, params=None, timeout=None):
        class _R:
            status_code = 404
            def json(self_):  # noqa: N805
                return {}
        return _R()

    monkeypatch.setattr(httpx, "get", fake_get)
    r = client.post("/v1/agents/ranker/activate", json={"version": "v1"})
    assert r.status_code == 409 and "not evaluated" in r.json()["error"]["message"]
