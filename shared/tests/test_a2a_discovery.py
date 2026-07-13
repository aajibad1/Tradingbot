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
    discover_agents,
    errors,
    find_agents_with_skill,
    install_a2a,
    roster_catalog,
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


def test_client_send_data_returns_task():
    # peer echoes len of the text; an empty data message has no text → len 0
    task = _peer_client().send_data({"q": "lookup"})
    assert task.status.state == "completed"
    assert task.status.message.text() == "echo: "
    assert task.artifacts[0].parts[1].data == {"len": 0}


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


# --- roster-wide discovery (discover_agents / find_agents_with_skill) ----------
def _card_client(name: str, skill_ids):
    """In-process A2AClient whose card advertises the given skills — same ASGI-via-
    MockTransport bridge as ``_peer_client``, parameterized per agent."""
    app = FastAPI()
    card = AgentCard(
        name=name, description="d", url=f"http://{name}/a2a", version="1.0",
        skills=[AgentSkill(id=s, name=s, description=s) for s in skill_ids],
    )
    install_a2a(app, card=card, handler=lambda m: [text_part("ok")])
    tc = TestClient(app)

    def _delegate(request: httpx.Request) -> httpx.Response:
        r = tc.request(request.method, request.url.path,
                       content=request.content, headers=request.headers)
        return httpx.Response(r.status_code, content=r.content,
                              headers={"content-type": r.headers.get("content-type",
                                                                     "application/json")})

    return A2AClient(f"http://{name}", transport=httpx.MockTransport(_delegate))


def _roster_factory(skills_by_name, *, unreachable=()):
    """Build a client_factory over a fake roster. Names in ``unreachable`` raise a
    transport error on card fetch (agent down); others serve their advertised card."""
    clients = {n: _card_client(n, ids) for n, ids in skills_by_name.items()}

    def factory(name):
        if name in unreachable:
            def _boom(request):  # connection refused on the card fetch
                raise httpx.ConnectError("down")
            return A2AClient(f"http://{name}", transport=httpx.MockTransport(_boom))
        return clients[name]

    return factory


def test_discover_agents_returns_each_reachable_card():
    factory = _roster_factory({"debate-service": ["verify-claim"],
                               "agent-evals": ["eval-verdict"]})
    cards = discover_agents(names=["debate-service", "agent-evals"], client_factory=factory)
    assert set(cards) == {"debate-service", "agent-evals"}
    assert cards["debate-service"].skills[0].id == "verify-claim"


def test_discover_agents_skips_unreachable_best_effort():
    factory = _roster_factory({"debate-service": ["verify-claim"], "agent-evals": []},
                              unreachable=["agent-evals"])
    cards = discover_agents(names=["debate-service", "agent-evals"], client_factory=factory)
    assert set(cards) == {"debate-service"}  # the down agent is omitted, not raised


def test_discover_agents_unknown_name_fails_loud():
    # default factory (client_for) resolves via base_url, which rejects a typo
    with pytest.raises(KeyError):
        discover_agents(names=["no-such-agent"])


def test_discover_agents_defaults_to_full_roster():
    # no names → every A2A_AGENTS member is attempted (verified via an injected
    # always-down factory — no real network, so the test can't flake against a
    # running local stack on the default ports).
    from shared.a2a import A2A_AGENTS

    attempted = []

    def factory(name):
        attempted.append(name)

        class _Down:
            def fetch_card(self):
                raise RuntimeError("down")

        return _Down()

    assert discover_agents(client_factory=factory) == {}
    assert sorted(attempted) == sorted(A2A_AGENTS)


def test_find_agents_with_skill_routes_by_capability():
    factory = _roster_factory({
        "debate-service": ["verify-claim"],
        "approval-gate-service": ["evaluate-action"],
        "agent-evals": ["eval-verdict", "verify-claim"],
    })
    names = ["debate-service", "approval-gate-service", "agent-evals"]
    found = find_agents_with_skill("verify-claim", names=names, client_factory=factory)
    assert found == ["agent-evals", "debate-service"]  # sorted; gate excluded
    assert find_agents_with_skill("nope", names=names, client_factory=factory) == []


def test_discover_agents_fetches_concurrently():
    # A barrier that only releases once ALL fetches are in flight — if discovery ran
    # sequentially the first fetch would block forever (the others never start), the
    # barrier would break, and those cards would drop. All present ⇒ truly parallel.
    import threading

    names = ["a", "b", "c", "d"]
    barrier = threading.Barrier(len(names), timeout=3)

    def factory(name):
        class _C:
            def fetch_card(self):
                barrier.wait()  # returns only when every worker has arrived
                return AgentCard(name=name, description="d", url=f"http://{name}/a2a",
                                 version="1.0", skills=[])
        return _C()

    cards = discover_agents(names=names, client_factory=factory)
    assert set(cards) == set(names)  # sequential fetch would time out → fewer cards


def test_roster_catalog_projects_cards_and_flags_unreachable():
    names = ["debate-service", "agent-evals"]
    factory = _roster_factory({"debate-service": ["verify-claim"], "agent-evals": []},
                              unreachable=["agent-evals"])
    cat = roster_catalog(names=names, client_factory=factory)
    assert cat["count"] == 1
    assert [a["name"] for a in cat["agents"]] == ["debate-service"]
    assert cat["agents"][0]["skills"][0]["id"] == "verify-claim"
    assert cat["roster"] == sorted(names)
    assert cat["unreachable"] == ["agent-evals"]  # down → listed, not raised
