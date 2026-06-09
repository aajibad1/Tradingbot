"""Debate orchestrator + deterministic judge tests (agents injected)."""
from __future__ import annotations

from debate import Position, default_judge, run_debate


def _prop(conf=0.8):
    return lambda claim, ctx: Position("proposer", "support", conf, "it's profitable",
                                       model_version="claude")


def _skeptic(stance, conf=0.7):
    return lambda claim, ctx, prop: Position("skeptic", stance, conf, f"{stance} arg",
                                             model_version="claude")


def test_no_refutation_supports_at_proposer_confidence():
    v = run_debate("signal X is real", {}, _prop(0.8), [_skeptic("uncertain", 0.0)])
    # one skeptic, uncertain (not refute) → support, discounted by the uncertainty
    assert v.decision == "support"
    assert 0 < v.confidence <= 0.8
    assert v.refuted_by == 0 and v.n_skeptics == 1


def test_majority_refutation_rejects():
    v = run_debate("signal X is real", {}, _prop(0.9),
                   [_skeptic("refute", 0.8), _skeptic("refute", 0.6), _skeptic("uncertain")])
    assert v.decision == "reject"
    assert v.refuted_by == 2 and v.n_skeptics == 3
    assert abs(v.confidence - 0.7) < 1e-6   # mean of refuter confidences


def test_minority_refutation_is_uncertain():
    v = run_debate("c", {}, _prop(0.9), [_skeptic("refute", 0.8), _skeptic("uncertain")])
    assert v.decision == "uncertain"


def test_clean_support_keeps_proposer_confidence():
    v = run_debate("c", {}, _prop(0.75), [_skeptic("support")])  # skeptic doesn't refute/uncertain
    # skeptic stance 'support' → not refute, not uncertain → frac=0, no unsure → full conf
    assert v.decision == "support" and v.confidence == 0.75


def test_transcript_and_models_captured():
    v = run_debate("c", {}, _prop(), [_skeptic("refute")])
    assert len(v.transcript) == 2
    assert v.transcript[0].role == "proposer"
    assert "claude" in v.model_versions


def test_default_judge_directly():
    prop = Position("proposer", "support", 0.6, "a", model_version="m")
    sk = [Position("skeptic", "refute", 0.9, "b", model_version="m")]
    v = default_judge("claim", prop, sk)
    assert v.decision == "reject" and v.confidence == 0.9
