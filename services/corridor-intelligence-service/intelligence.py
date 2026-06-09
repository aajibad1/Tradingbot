"""Africa corridor intelligence — predict corridor reliability / settlement
friction / regulatory risk to enrich the corridor-engine's settlement-confidence.

Perplexity (Deep Research + Finance) is the *market-intelligence* brain here:
real-time research on country policy, FX drift, payout-rail reliability. It is
ADVISORY — it adjusts a confidence score; the deterministic corridor scorer and
the risk-engine still own the hard go/no-go. The Perplexity call is injected so
this stays testable, and there's a deterministic baseline when no key is set
(graceful degrade, like sentiment-service).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Source currency → ISO country (drives the baseline + research context).
_CCY_COUNTRY = {"NGN": "NG", "ZAR": "ZA", "KES": "KE", "GHS": "GH", "USD": ""}
_LAUNCH = {"NG", "ZA", "KE", "GH"}            # markets we've vetted
_BLOCKED = {"KP", "IR", "SY", "CU"}           # sanctioned placeholder
_RISK = ("low", "medium", "high")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def source_country(corridor: str) -> str:
    src = corridor.split("->", 1)[0].strip().upper() if "->" in corridor else ""
    return _CCY_COUNTRY.get(src, "")


@dataclass
class CorridorIntel:
    corridor: str
    reliability_score: float          # 0..1 (feeds settlement confidence)
    settlement_friction_bps: float    # estimated extra cost from delay/rail
    regulatory_risk: str              # low | medium | high
    summary: str
    source: str                       # 'perplexity' | 'baseline'
    # Grounding/audit trail: cited sources + the model that produced this.
    sources: list[str] = field(default_factory=list)
    model_version: str | None = None


def _baseline(corridor: str) -> CorridorIntel:
    cc = source_country(corridor)
    if cc in _BLOCKED:
        return CorridorIntel(corridor, 0.1, 200.0, "high", f"{cc} sanctioned/blocked", "baseline")
    if cc in _LAUNCH:
        return CorridorIntel(corridor, 0.7, 30.0, "medium",
                             f"{cc} is a vetted launch market", "baseline")
    return CorridorIntel(corridor, 0.5, 60.0, "high",
                         f"{cc or 'unknown'} not in the launch set — review", "baseline")


def assess_corridor(corridor: str, research=None) -> CorridorIntel:
    """Assess a corridor. ``research(corridor) -> dict | None`` is the injected
    Perplexity call; on None (no key, error) we fall back to the deterministic
    baseline. Expected research keys: reliability (0..1), regulatory_risk
    (low/medium/high), friction_bps (float), summary (str)."""
    raw = research(corridor) if research is not None else None
    if not raw:
        return _baseline(corridor)
    reg = str(raw.get("regulatory_risk", "high")).lower()
    return CorridorIntel(
        corridor=corridor,
        reliability_score=_clamp(float(raw.get("reliability", 0.5))),
        settlement_friction_bps=max(0.0, float(raw.get("friction_bps", 60.0))),
        regulatory_risk=reg if reg in _RISK else "high",
        summary=str(raw.get("summary", ""))[:600],
        source="perplexity",
        sources=list(raw.get("citations", []) or []),
        model_version=raw.get("model_version"),
    )
