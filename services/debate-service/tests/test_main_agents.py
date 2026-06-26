"""main._agents resolution + fail-soft contract.

The panel must never crash the /debate endpoint: no key → abstain; key set but the
Claude SDK missing or unconstructable → abstain (logged), not a 500."""
from __future__ import annotations

import main


def test_no_key_returns_abstaining_panel(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    proposer, skeptics = main._agents(2)
    assert len(skeptics) == 2
    prop = proposer("c", {})
    assert prop.stance == "support" and prop.model_version is None
    assert skeptics[0]("c", {}, prop).stance == "uncertain"


def test_key_set_but_sdk_unavailable_fails_soft(monkeypatch):
    # anthropic SDK is not installed in the test env, so constructing the live
    # client raises ImportError — _agents must degrade to abstention, not raise.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    proposer, skeptics = main._agents(3)
    assert len(skeptics) == 3
    prop = proposer("c", {})
    assert prop.stance == "support" and prop.model_version is None  # abstaining
    assert all(s("c", {}, prop).model_version is None for s in skeptics)


def test_debate_endpoint_returns_verdict_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = main.debate(main.DebateIn(claim="funding carry is profitable", n_skeptics=2))
    assert out["decision"] in {"support", "reject", "uncertain"}
    assert out["n_skeptics"] == 2
    assert len(out["transcript"]) == 3  # proposer + 2 skeptics
