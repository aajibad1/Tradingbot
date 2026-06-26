"""Claude-backed persona tests — exercised with a fake LLMClient, no SDK/network.

The personas consult an injected ``LLMClient``; a canned-reply client drives the
exact parse → clamp → map path production runs against Claude, plus the fail-soft
path when the client raises. The personas never gate — they only shape Positions
the deterministic judge weighs."""
from __future__ import annotations

from claude_agents import _SKEPTIC_LENSES, build_claude_agents
from debate import Position, run_debate


class FakeClient:
    """Canned-reply LLMClient: returns the next queued dict per ``complete`` call,
    or raises if a queued entry is an Exception (to drive the abstain path)."""

    def __init__(self, model: str, replies: list):
        self.model = model
        self._replies = list(replies)
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, schema: dict) -> dict:
        self.calls.append({"system": system, "user": user, "schema": schema})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_full_debate_maps_replies_to_verdict():
    client = FakeClient("claude-opus-4-8", [
        {"stance": "support", "confidence": 0.8, "argument": "edge is real"},
        {"stance": "refute", "confidence": 0.9, "argument": "data is stale"},
        {"stance": "refute", "confidence": 0.7, "argument": "slippage eats it"},
    ])
    proposer, skeptics = build_claude_agents(2, client=client)
    verdict = run_debate("funding carry is profitable", {"venue": "x"}, proposer, skeptics)

    assert verdict.decision == "reject"          # 2/2 refute → strict majority
    assert verdict.refuted_by == 2 and verdict.n_skeptics == 2
    assert verdict.model_versions == ["claude-opus-4-8"] * 3
    assert verdict.transcript[0].role == "proposer"
    assert verdict.transcript[0].stance == "support"


def test_confidence_is_clamped_and_stance_defended():
    # confidence out of [0,1] is clamped; an out-of-allowed stance falls back safely.
    client = FakeClient("m", [
        {"stance": "bogus", "confidence": 5.0, "argument": "x"},        # proposer
        {"stance": "refute", "confidence": -3.0, "argument": "y"},       # skeptic
    ])
    proposer, skeptics = build_claude_agents(1, client=client)
    prop = proposer("c", {})
    skeptic_pos = skeptics[0]("c", {}, prop)

    assert prop.stance == "support"     # only allowed proposer stance
    assert prop.confidence == 1.0       # clamped from 5.0
    assert skeptic_pos.confidence == 0.0  # clamped from -3.0


def test_missing_argument_gets_placeholder():
    client = FakeClient("m", [{"stance": "support", "confidence": 0.5}])
    proposer, _ = build_claude_agents(1, client=client)
    prop = proposer("c", {})
    assert prop.argument == "(no argument given)"


def test_llm_error_fails_soft_to_abstention():
    client = FakeClient("m", [RuntimeError("api down"), RuntimeError("api down")])
    proposer, skeptics = build_claude_agents(1, client=client)
    prop = proposer("c", {})
    skeptic_pos = skeptics[0]("c", {}, prop)

    assert prop.stance == "support" and prop.confidence == 0.5
    assert prop.model_version is None            # abstention is unsigned
    assert skeptic_pos.stance == "uncertain"
    assert "abstaining" in skeptic_pos.argument


def test_skeptics_get_distinct_lenses():
    n = 3
    client = FakeClient("m", [{"stance": "support", "confidence": 0.7, "argument": "a"}]
                        + [{"stance": "uncertain", "confidence": 0.5, "argument": "b"}] * n)
    proposer, skeptics = build_claude_agents(n, client=client)
    prop = proposer("c", {})
    for s in skeptics:
        s("c", {}, prop)

    skeptic_systems = [c["system"] for c in client.calls[1:]]
    assert len(set(skeptic_systems)) == n        # each skeptic has a unique lens prompt
    # and each system embeds one of the canonical lenses
    for system in skeptic_systems:
        assert any(lens in system for _, lens in _SKEPTIC_LENSES)


def test_more_skeptics_than_lenses_cycles():
    n = len(_SKEPTIC_LENSES) + 2
    client = FakeClient("m", [{"stance": "support", "confidence": 0.7, "argument": "a"}]
                        + [{"stance": "uncertain", "confidence": 0.5, "argument": "b"}] * n)
    proposer, skeptics = build_claude_agents(n, client=client)
    assert len(skeptics) == n                    # cycles lenses, never raises IndexError
    prop = proposer("c", {})
    for s in skeptics:
        assert isinstance(s("c", {}, prop), Position)
