"""Reconcile Hummingbot's internal position view against exchange truth.

Hummingbot reports the bot's view; the exchange reports actual settled state.
Drift between them is the leading indicator of a bug or a stale order. The
reconciler:
  1. Pulls open positions from Hummingbot.
  2. Pulls positions from each exchange via CCXT (read-only).
  3. Diffs by (exchange, asset). Emits a Discrepancy for each mismatch.
  4. Mismatches > $50 USD or > 5% notional trip a Pub/Sub risk alert
     (severity='warn'); persistent mismatches over 3 cycles trip 'critical'.

This runs periodically — every 60s by default — not inline with execution.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


_DISCREPANCY_USD_THRESHOLD = 50.0
_DISCREPANCY_PCT_THRESHOLD = 0.05  # 5%


@dataclass(frozen=True)
class PositionRow:
    exchange: str
    asset: str
    size: float
    notional_usd: float

    @property
    def key(self) -> tuple[str, str]:
        return (self.exchange.lower(), self.asset.upper())


@dataclass
class Discrepancy:
    exchange: str
    asset: str
    hummingbot_size: float
    exchange_size: float
    diff_size: float
    diff_notional_usd: float
    severity: str  # 'ok' | 'warn' | 'critical'
    observed_at: datetime


def _index(rows: Iterable[PositionRow]) -> dict[tuple[str, str], PositionRow]:
    return {r.key: r for r in rows}


def reconcile(
    hummingbot_positions: Iterable[PositionRow],
    exchange_positions: Iterable[PositionRow],
    persistent_streak: dict[tuple[str, str], int] | None = None,
) -> list[Discrepancy]:
    """Return one Discrepancy per mismatched (exchange, asset) pair.

    `persistent_streak` is the caller-owned counter of consecutive cycles
    a given pair has been mismatched — used to escalate warn → critical.
    """
    hum = _index(hummingbot_positions)
    exc = _index(exchange_positions)
    all_keys = set(hum) | set(exc)
    now = datetime.utcnow()
    out: list[Discrepancy] = []

    for key in sorted(all_keys):
        h = hum.get(key)
        e = exc.get(key)
        h_size = h.size if h else 0.0
        e_size = e.size if e else 0.0
        h_notional = h.notional_usd if h else 0.0
        e_notional = e.notional_usd if e else 0.0
        diff_size = h_size - e_size
        diff_notional = abs(h_notional - e_notional)

        if diff_size == 0 and diff_notional == 0:
            if persistent_streak is not None:
                persistent_streak.pop(key, None)
            continue

        ref_notional = max(abs(h_notional), abs(e_notional), 1.0)
        pct_diff = diff_notional / ref_notional

        if diff_notional <= _DISCREPANCY_USD_THRESHOLD and pct_diff <= _DISCREPANCY_PCT_THRESHOLD:
            severity = "ok"
        else:
            severity = "warn"

        if persistent_streak is not None:
            persistent_streak[key] = persistent_streak.get(key, 0) + 1
            if persistent_streak[key] >= 3 and severity == "warn":
                severity = "critical"

        out.append(
            Discrepancy(
                exchange=key[0],
                asset=key[1],
                hummingbot_size=h_size,
                exchange_size=e_size,
                diff_size=diff_size,
                diff_notional_usd=diff_notional,
                severity=severity,
                observed_at=now,
            )
        )
    return out


def hummingbot_positions_from_api(rows: list[dict[str, Any]]) -> list[PositionRow]:
    """Adapt the Hummingbot REST response into PositionRow objects."""
    out: list[PositionRow] = []
    for row in rows:
        try:
            size = float(row["size"])
            price = float(row["mark_price"])
            out.append(
                PositionRow(
                    exchange=row["exchange"],
                    asset=row["asset"],
                    size=size,
                    notional_usd=size * price,
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.exception("malformed hummingbot position row: %s", row)
    return out
