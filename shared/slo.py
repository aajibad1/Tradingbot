"""SLO error-budget + burn-rate evaluation (reliability as a provable feature).

Trust/reliability is the #1 product priority (docs/MASTER_STRATEGY.md) and
docs/SLOS.md states the targets — but a target in a doc proves nothing. This is
the code that turns observed good/total event counts into an error-budget
position and a burn-rate alerting decision, so "are we meeting our SLO?" has a
deterministic, tested answer.

Pure + dependency-free: counts in, a verdict out. A metrics source (BigQuery
event lag, status-service probe history) feeds it later; the math is identical
whether the numbers are live or synthetic.

Error-budget model (standard SRE):
  objective o      e.g. 0.999  → allowed failure fraction = 1 - o
  failure_fraction = bad / total           (observed over the window)
  burn_rate        = failure_fraction / (1 - o)
                     1.0 = exhaust the whole window's budget exactly on time;
                     >1  = burning faster than sustainable; <1 = under budget.
  budget_remaining = 1 - burn_rate         (fraction of the window's budget left)

Multi-window burn-rate alerting (Google SRE Workbook): a high burn rate over a
short window pages; a moderate burn rate over a long window tickets. Thresholds
are expressed as burn-rate multiples so they are independent of the objective.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SLOSeverity(str, Enum):
    OK = "ok"
    TICKET = "ticket"   # moderate, sustained burn — investigate, no page
    PAGE = "page"       # fast burn — wake someone


# Burn-rate thresholds (multiples of sustainable burn). Page if we'd exhaust the
# budget >14x too fast (≈2% of a 30d budget in 1h); ticket at >1x sustained.
_PAGE_BURN_RATE = 14.4
_TICKET_BURN_RATE = 1.0


@dataclass(frozen=True)
class SLOTarget:
    name: str
    objective: float          # e.g. 0.999 (must be in (0, 1))
    window_days: int
    description: str = ""

    def __post_init__(self) -> None:
        if not 0.0 < self.objective < 1.0:
            raise ValueError(f"objective must be in (0,1), got {self.objective}")


@dataclass(frozen=True)
class SLOStatus:
    name: str
    objective: float
    total: int
    good: int
    success_rate: float | None        # None when total == 0 (no data)
    failure_fraction: float
    burn_rate: float                  # see module docstring
    budget_remaining: float           # 1 - burn_rate (can go negative = over budget)
    breaching: bool                   # success_rate < objective
    severity: SLOSeverity

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "objective": self.objective,
            "total": self.total,
            "good": self.good,
            "success_rate": round(self.success_rate, 6) if self.success_rate is not None else None,
            "burn_rate": round(self.burn_rate, 4),
            "budget_remaining": round(self.budget_remaining, 4),
            "breaching": self.breaching,
            "severity": self.severity.value,
        }


def evaluate_slo(target: SLOTarget, good: int, total: int) -> SLOStatus:
    """Evaluate one SLO over ``good``/``total`` events observed in its window.

    ``total == 0`` (no traffic / no data) is reported as OK with a null success
    rate and zero burn — absence of data is not an SLO breach. Negative or
    good>total inputs are clamped defensively (a metrics bug must not crash the
    reliability layer that is supposed to catch problems).
    """
    good = max(0, min(good, total)) if total > 0 else 0
    total = max(0, total)
    allowed_failure = 1.0 - target.objective

    if total == 0:
        return SLOStatus(
            name=target.name, objective=target.objective, total=0, good=0,
            success_rate=None, failure_fraction=0.0, burn_rate=0.0,
            budget_remaining=1.0, breaching=False, severity=SLOSeverity.OK,
        )

    success_rate = good / total
    failure_fraction = 1.0 - success_rate
    burn_rate = failure_fraction / allowed_failure if allowed_failure > 0 else 0.0
    budget_remaining = 1.0 - burn_rate
    breaching = success_rate < target.objective

    if burn_rate >= _PAGE_BURN_RATE:
        severity = SLOSeverity.PAGE
    elif burn_rate > _TICKET_BURN_RATE:
        severity = SLOSeverity.TICKET
    else:
        severity = SLOSeverity.OK

    return SLOStatus(
        name=target.name, objective=target.objective, total=total, good=good,
        success_rate=success_rate, failure_fraction=failure_fraction,
        burn_rate=burn_rate, budget_remaining=budget_remaining,
        breaching=breaching, severity=severity,
    )


# Catalog mirroring docs/SLOS.md — the targets live in code so they are a single
# source of truth the evaluator (and tests) bind to, not just prose in a doc.
DEFAULT_SLOS: dict[str, SLOTarget] = {
    "control_plane_availability": SLOTarget(
        "control_plane_availability", 0.999, 30,
        "core-api + dashboard-api 200-vs-503 availability",
    ),
    "event_processing_lag": SLOTarget(
        "event_processing_lag", 0.95, 30,
        "opportunity → arb-approved within p95 < 2s (good = under threshold)",
    ),
    "market_data_freshness": SLOTarget(
        "market_data_freshness", 0.99, 30,
        "health:latency:* staleness < 30s (good = fresh)",
    ),
    "webhook_idempotency": SLOTarget(
        "webhook_idempotency", 0.999, 30,
        "Stripe/Clerk webhook dedup correctness",
    ),
}


def evaluate_all(observations: dict[str, tuple[int, int]],
                 catalog: dict[str, SLOTarget] | None = None) -> dict[str, dict]:
    """Evaluate a batch of SLOs. ``observations`` maps SLO name → (good, total);
    names absent from the catalog are ignored. Returns name → status dict."""
    cat = catalog or DEFAULT_SLOS
    out: dict[str, dict] = {}
    for name, (good, total) in observations.items():
        if name in cat:
            out[name] = evaluate_slo(cat[name], good, total).to_dict()
    return out


def worst_severity(statuses: dict[str, dict]) -> SLOSeverity:
    """Roll a batch of status dicts up to the most severe alerting level."""
    order = {SLOSeverity.OK: 0, SLOSeverity.TICKET: 1, SLOSeverity.PAGE: 2}
    worst = SLOSeverity.OK
    for s in statuses.values():
        sev = SLOSeverity(s["severity"])
        if order[sev] > order[worst]:
            worst = sev
    return worst
