"""Learning-loop journal: joins journaled signal predictions to realized trade
fills on ``signal_id == opportunity_id`` and scores each pair the moment both
sides arrive. Pure in-memory state; no I/O — main.py wires Pub/Sub around it.

Without this join the replay/label functions in replay.py have no substrate:
a detected signal and its eventual trade fill arrive independently (from
signal-engine's journal and paper-trader's fills, respectively), on their own
topics, at unrelated times. This is what turns "a signal was emitted" and
"a trade closed" into "this signal was a true/false positive."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.models.movement_signal import MovementSignal
from shared.models.trade import Trade

from replay import SignalOutcome, label


@dataclass
class ScoredSignal:
    signal_id: str
    outcome: SignalOutcome
    label: str


class SignalLedger:
    """Not thread-safe by itself — callers (main.py) guard access with a lock."""

    def __init__(self) -> None:
        self._pending: dict[str, MovementSignal] = {}
        self._scored: list[ScoredSignal] = []

    def record_signal(self, signal: MovementSignal) -> None:
        self._pending[signal.signal_id] = signal

    def record_fill(self, trade: Trade) -> ScoredSignal | None:
        """Join a fill to its originating signal by signal_id == opportunity_id
        and score it. Returns None on a miss (e.g. a market-neutral fill, or a
        signal that was never journaled) — a miss is not an error, just nothing
        to score."""
        signal = self._pending.pop(trade.opportunity_id, None)
        if signal is None:
            return None
        notional_usd = min((leg.size * leg.fill_price for leg in trade.legs), default=0.0)
        realized_bps = (trade.net_pnl_usd / notional_usd * 10_000.0) if notional_usd else 0.0
        return self._score(signal, realized_bps=realized_bps, reached_execution=True)

    def sweep_expired(self, now: datetime | None = None) -> list[ScoredSignal]:
        """Signals whose expected setup window has elapsed with no matching
        fill are 'decayed' — refused by risk-engine, refused below the publish
        threshold, or simply never acted on. Swept out so /report reflects the
        full picture, not just executed fills. A signal with expiry_ms<=0 (no
        expiry data) is never swept — decaying it would be a guess, not a fact."""
        now = now or datetime.utcnow()
        expired_ids = [
            sid for sid, sig in self._pending.items()
            if sig.expiry_ms > 0 and now >= sig.detected_at + timedelta(milliseconds=sig.expiry_ms)
        ]
        swept = []
        for sid in expired_ids:
            signal = self._pending.pop(sid)
            swept.append(self._score(signal, realized_bps=0.0, reached_execution=False))
        return swept

    def _score(self, signal: MovementSignal, *, realized_bps: float, reached_execution: bool) -> ScoredSignal:
        outcome = SignalOutcome(
            family=signal.family, regime=signal.regime or "unknown",
            direction=signal.direction, gross_edge_bps=signal.gross_edge_bps,
            realized_bps=realized_bps, reached_execution=reached_execution,
        )
        scored = ScoredSignal(signal_id=signal.signal_id, outcome=outcome, label=label(outcome))
        self._scored.append(scored)
        return scored

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def scored(self) -> list[ScoredSignal]:
        return list(self._scored)
