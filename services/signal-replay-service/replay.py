"""Signal replay / evaluation (hybrid learning layer, deterministic).

Given detected signals paired with their realized outcomes, labels each
(survived → execution / decayed before / false positive) and aggregates the
quality metrics the brief tracks — chiefly the false-positive rate and
profitability by signal family and regime. This is the labeling substrate for
later model training (BigQuery facts) and a backtest scorer for the signal-engine.
Pure functions; no I/O.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class SignalOutcome:
    family: str
    regime: str
    direction: str            # 'long' | 'short'
    gross_edge_bps: float     # edge at detection
    realized_bps: float       # realized move over the hold (signed, after costs)
    reached_execution: bool = True   # did it survive to execution


# A signal is a "false positive" if it survived to execution but lost money.
def label(o: SignalOutcome) -> str:
    if not o.reached_execution:
        return "decayed"          # expired before we could act
    return "true_positive" if o.realized_bps > 0 else "false_positive"


@dataclass
class FamilyStats:
    n: int
    win_rate: float
    mean_realized_bps: float
    false_positive_rate: float


@dataclass
class ReplayReport:
    n: int
    false_positive_rate: float
    decay_rate: float
    mean_realized_bps: float
    by_family: dict = field(default_factory=dict)
    by_regime: dict = field(default_factory=dict)


def _agg(outcomes: list[SignalOutcome]) -> FamilyStats:
    executed = [o for o in outcomes if o.reached_execution]
    n = len(outcomes)
    wins = sum(1 for o in executed if o.realized_bps > 0)
    fps = sum(1 for o in executed if o.realized_bps <= 0)
    realized = [o.realized_bps for o in executed]
    return FamilyStats(
        n=n,
        win_rate=round(wins / len(executed), 4) if executed else 0.0,
        mean_realized_bps=round(statistics.mean(realized), 2) if realized else 0.0,
        false_positive_rate=round(fps / len(executed), 4) if executed else 0.0,
    )


def replay(outcomes: list[SignalOutcome]) -> ReplayReport:
    n = len(outcomes)
    if n == 0:
        return ReplayReport(0, 0.0, 0.0, 0.0)
    executed = [o for o in outcomes if o.reached_execution]
    fps = sum(1 for o in executed if o.realized_bps <= 0)
    decayed = sum(1 for o in outcomes if not o.reached_execution)
    realized = [o.realized_bps for o in executed]

    by_family: dict[str, list[SignalOutcome]] = defaultdict(list)
    by_regime: dict[str, list[SignalOutcome]] = defaultdict(list)
    for o in outcomes:
        by_family[o.family].append(o)
        by_regime[o.regime].append(o)

    return ReplayReport(
        n=n,
        false_positive_rate=round(fps / len(executed), 4) if executed else 0.0,
        decay_rate=round(decayed / n, 4),
        mean_realized_bps=round(statistics.mean(realized), 2) if realized else 0.0,
        by_family={k: _agg(v).__dict__ for k, v in by_family.items()},
        by_regime={k: _agg(v).__dict__ for k, v in by_regime.items()},
    )
