"""Model unexpected spread collapse — when the arb edge closes before our hold horizon.

Heuristic: in any 1-hour window after entry there's a small probability the spread
halves. When it does, we exit early — funding hasn't accrued for the full term and
PnL takes the hit. We sample exit time as a coarse estimate, not as a precise sim.
"""

from __future__ import annotations

import random

# Per-hour probability that the spread halves and we exit. Calibrated so that
# over a 24h horizon roughly 30% of positions exit early — see
# tests/test_comprehensive.py::test_spread_collapse_early_exit.
_HOURLY_COLLAPSE_PROBABILITY = 0.015


def sample_early_exit(
    planned_hold_hours: float,
    rng: random.Random | None = None,
) -> float | None:
    """Return early-exit hours, or None if the position survives the planned horizon."""
    rng = rng or random.Random()
    for hour in range(1, max(1, int(planned_hold_hours))):
        if rng.random() < _HOURLY_COLLAPSE_PROBABILITY:
            return float(hour)
    return None
