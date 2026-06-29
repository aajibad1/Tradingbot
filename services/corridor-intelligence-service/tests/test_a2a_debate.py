"""corridor-intelligence → debate-service adversarial verification over A2A.

Opt-in (A2A_DEBATE_SERVICE_URL) and fail-soft: the debate verdict is purely
additive on /assess and never blocks or alters the assessment."""
from __future__ import annotations

import main
from fastapi.testclient import TestClient
from shared.a2a import Message, Task, TaskStatus, data_part

client = TestClient(main.app)


def _stub_debate(monkeypatch, verdict: dict | None = None, *, boom: bool = False):
    class _Stub:
        def __init__(self, base, timeout=None):
            pass

        def send_text(self, claim, **_):
            if boom:
                raise RuntimeError("debate down")
            reply = Message(role="agent", parts=[data_part(verdict or {})], message_id="r")
            return Task(id="t", context_id="c",
                        status=TaskStatus(state="completed", message=reply))

    monkeypatch.setattr(main, "A2AClient", _Stub)


def test_assess_has_no_debate_key_when_unconfigured(monkeypatch):
    monkeypatch.delenv("A2A_DEBATE_SERVICE_URL", raising=False)
    out = client.post("/assess", json={"corridor": "NGN->ZAR"}).json()
    assert out["corridor"] == "NGN->ZAR" and "reliability_score" in out
    assert "debate" not in out  # default behavior unchanged


def test_assess_attaches_debate_verdict_when_configured(monkeypatch):
    monkeypatch.setenv("A2A_DEBATE_SERVICE_URL", "http://debate")
    _stub_debate(monkeypatch, {"decision": "support", "confidence": 0.7,
                               "refuted_by": 0, "n_skeptics": 3})
    out = client.post("/assess", json={"corridor": "NGN->ZAR"}).json()
    assert out["debate"] == {"decision": "support", "confidence": 0.7,
                             "refuted_by": 0, "n_skeptics": 3}


def test_debate_unreachable_is_fail_soft(monkeypatch):
    monkeypatch.setenv("A2A_DEBATE_SERVICE_URL", "http://debate")
    _stub_debate(monkeypatch, boom=True)
    out = client.post("/assess", json={"corridor": "NGN->ZAR"}).json()
    assert "debate" not in out and "reliability_score" in out  # assessment intact


def test_empty_verdict_is_ignored(monkeypatch):
    monkeypatch.setenv("A2A_DEBATE_SERVICE_URL", "http://debate")
    _stub_debate(monkeypatch, {})  # no decision → no annotation
    out = client.post("/assess", json={"corridor": "NGN->ZAR"}).json()
    assert "debate" not in out


def test_discovery_opt_in_routes_by_verify_claim(monkeypatch):
    # No explicit URL, but discovery is opted in → route to whoever advertises
    # the verify-claim skill, then attach its verdict.
    monkeypatch.delenv("A2A_DEBATE_SERVICE_URL", raising=False)
    monkeypatch.setenv("A2A_DISCOVER_DEBATE", "1")
    seen = {}

    def _fake_discovery(skill_id, **kwargs):
        seen["skill"] = skill_id
        return ["debate-service"]

    monkeypatch.setattr(main, "find_agents_with_skill", _fake_discovery)
    _stub_debate(monkeypatch, {"decision": "reject", "confidence": 0.6,
                               "refuted_by": 2, "n_skeptics": 3})
    out = client.post("/assess", json={"corridor": "NGN->ZAR"}).json()
    assert out["debate"]["decision"] == "reject"
    assert seen["skill"] == "verify-claim"  # routed by capability


def test_discovery_off_by_default_even_with_provider(monkeypatch):
    # Discovery must NOT run unless explicitly enabled — verification stays opt-in.
    monkeypatch.delenv("A2A_DEBATE_SERVICE_URL", raising=False)
    monkeypatch.delenv("A2A_DISCOVER_DEBATE", raising=False)

    def _boom(*a, **k):
        raise AssertionError("discovery must not run unless A2A_DISCOVER_DEBATE is set")

    monkeypatch.setattr(main, "find_agents_with_skill", _boom)
    out = client.post("/assess", json={"corridor": "NGN->ZAR"}).json()
    assert "debate" not in out
