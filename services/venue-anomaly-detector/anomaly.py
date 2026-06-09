"""Venue anomaly detection (hybrid system, deterministic).

Flags degraded feeds — stale books, spread jumps, sequence gaps, update-rate
drops, abnormal rejections — so the signal-engine/regime can suppress and the
risk layer can tighten. Reduces false positives + protects execution quality
(the brief's success metric). Rule-based first; explainable, no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"


# Thresholds.
_STALE_MS = 5_000.0           # no fresh quote in this long → stale
_SPREAD_JUMP_X = 3.0          # spread > Nx the expected band
_SEQ_GAP_RATE = 0.05          # >5% sequence-number gaps
_UPDATE_DROP = 0.5           # update rate < 50% of baseline
_REJECT_RATE = 0.2          # >20% order rejections


@dataclass
class VenueSignals:
    venue: str
    quote_staleness_ms: float = 0.0
    spread_bps: float = 0.0
    expected_spread_bps: float = 0.0
    sequence_gap_rate: float = 0.0
    update_rate_ratio: float = 1.0   # current / baseline update frequency
    rejection_rate: float = 0.0


@dataclass
class Anomaly:
    kind: str
    severity: Severity
    detail: str


@dataclass
class VenueAnomalyReport:
    venue: str
    status: Severity
    anomalies: list[Anomaly] = field(default_factory=list)
    degraded: bool = False


def detect(s: VenueSignals) -> VenueAnomalyReport:
    a: list[Anomaly] = []

    if s.quote_staleness_ms > _STALE_MS:
        a.append(Anomaly("stale_quotes", Severity.CRITICAL,
                         f"no quote for {s.quote_staleness_ms:.0f}ms"))
    if s.expected_spread_bps > 0 and s.spread_bps > _SPREAD_JUMP_X * s.expected_spread_bps:
        a.append(Anomaly("spread_jump", Severity.WARN,
                         f"spread {s.spread_bps:.1f} > {_SPREAD_JUMP_X}x expected {s.expected_spread_bps:.1f}"))
    if s.sequence_gap_rate > _SEQ_GAP_RATE:
        a.append(Anomaly("sequence_gaps", Severity.CRITICAL,
                         f"gap rate {s.sequence_gap_rate:.0%}"))
    if s.update_rate_ratio < _UPDATE_DROP:
        a.append(Anomaly("update_rate_drop", Severity.WARN,
                         f"update rate {s.update_rate_ratio:.0%} of baseline"))
    if s.rejection_rate > _REJECT_RATE:
        a.append(Anomaly("high_rejection", Severity.CRITICAL,
                         f"rejection rate {s.rejection_rate:.0%}"))

    status = Severity.OK
    if any(x.severity is Severity.CRITICAL for x in a):
        status = Severity.CRITICAL
    elif a:
        status = Severity.WARN
    return VenueAnomalyReport(venue=s.venue, status=status, anomalies=a,
                              degraded=status is not Severity.OK)
