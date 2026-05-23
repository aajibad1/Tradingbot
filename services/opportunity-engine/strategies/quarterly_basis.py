"""Quarterly-futures basis trade (Phase 2, disabled by default).

This is distinct from the existing ``spot_perp_basis.py``, which captures
the perp-vs-spot premium for mean-reverting carry. This strategy targets
the *fixed* basis premium on quarterly-expiry futures: long spot + short
the quarterly future, hold until expiry for the locked-in yield.

Why disabled by default:
  * None of the current US-domiciled venues we trade (Coinbase, Kraken,
    Crypto.com, Binance.US, Hyperliquid) offers a quarterly future we can
    legally and safely route through Hummingbot today.
  * Quarterly futures need a different ``FundingRate`` shape (they don't
    fund; they have a settlement date) — opening that contract requires a
    sibling model in shared/models. Adding the model in Phase 1 would be
    premature.

The strategy ships behind ``ENABLE_BASIS_TRADE``. While disabled, the
``evaluate()`` method returns an empty iterable — it is NOT registered in
``strategies/__init__.py`` to avoid wiring a no-op into the hot path.
Enabling it for real Phase-2 work requires:

  1. Add a ``QuarterlyFuture`` model in shared/models.
  2. Source data in funding-rate-service (or a new ``futures-service``).
  3. Annualised-basis computation here.
  4. Register the strategy in ``main.py`` and ``strategies/__init__.py``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from models import MarketSnapshot
from scorer import ScoredCandidate
from strategies.base import Strategy


_MIN_BASIS_ANNUALIZED_PCT = 8.0  # threshold to enter on quarterly basis


def _enabled() -> bool:
    return os.environ.get("ENABLE_BASIS_TRADE", "false").lower() == "true"


class QuarterlyBasisStrategy(Strategy):
    name = "quarterly_basis"

    def evaluate(self, snapshot: MarketSnapshot) -> Iterable[ScoredCandidate]:
        if not _enabled():
            return []
        # When this lands for real: pull quarterly futures from the snapshot,
        # compute annualized basis = (futures_price - spot_price) / spot_price
        #                            * (365 / days_to_expiry),
        # filter by _MIN_BASIS_ANNUALIZED_PCT, and emit ScoredCandidates with
        # funding_rate_bps_per_period left at 0 (basis trades do not fund).
        return []
