"""Multi-agent debate (adversarial verification) — intelligence plane, ADVISORY.

A proposer argues FOR a claim; skeptics try to REFUTE it; a judge weighs the
disagreement into a calibrated verdict. Used to cut false positives on slower
advisory decisions (opportunity quality, corridor reliability, regime, ops) —
NEVER in the per-trade hot path, and NEVER the final authority: the deterministic
risk-engine still decides. The full transcript + model versions are returned so
every verdict is auditable.

Agents are injected callables, so the orchestrator is fully testable without any
LLM; prod wires Claude (reasoning) + Perplexity (cited evidence) personas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Decision = str  # 'support' | 'reject' | 'uncertain'


@dataclass
class Position:
    role: str                 # 'proposer' | 'skeptic'
    stance: str               # 'support' | 'refute' | 'uncertain'
    confidence: float         # 0..1
    argument: str
    sources: list[str] = field(default_factory=list)
    model_version: str | None = None


@dataclass
class Verdict:
    claim: str
    decision: Decision
    confidence: float
    refuted_by: int
    n_skeptics: int
    transcript: list[Position]
    model_versions: list[str] = field(default_factory=list)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def default_judge(claim: str, proposer: Position, skeptics: list[Position]) -> Verdict:
    """Deterministic aggregation — testable, explainable, no LLM. A majority of
    refutations rejects; none supports (at the proposer's confidence); a minority
    is uncertain. (A pluggable LLM judge can replace this later.)"""
    n = len(skeptics)
    refuters = [s for s in skeptics if s.stance == "refute"]
    frac = len(refuters) / n if n else 0.0
    models = [p.model_version for p in [proposer, *skeptics] if p.model_version]

    if frac > 0.5:  # strict majority refutes
        conf = sum(s.confidence for s in refuters) / len(refuters)
        decision, confidence = "reject", conf
    elif frac == 0.0:
        # No skeptic refuted — support, discounted slightly by any uncertainty.
        unsure = sum(1 for s in skeptics if s.stance == "uncertain")
        decision = "support"
        confidence = proposer.confidence * (1.0 - 0.5 * (unsure / n if n else 0.0))
    else:
        decision = "uncertain"
        confidence = 0.5

    return Verdict(
        claim=claim, decision=decision, confidence=round(_clamp(confidence), 4),
        refuted_by=len(refuters), n_skeptics=n,
        transcript=[proposer, *skeptics], model_versions=models,
    )


def run_debate(claim, context, proposer, skeptics, judge=default_judge) -> Verdict:
    """Orchestrate one debate.

    ``proposer(claim, context) -> Position`` (stance should be 'support').
    ``skeptics`` is a list of ``skeptic(claim, context, proposer_position) -> Position``
    (stance 'refute' or 'uncertain'). ``judge`` aggregates into a Verdict.
    """
    prop = proposer(claim, context)
    skeptic_positions = [s(claim, context, prop) for s in skeptics]
    return judge(claim, prop, skeptic_positions)
