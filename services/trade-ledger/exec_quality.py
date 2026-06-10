"""Execution-quality instrumentation (AI Phase A — route calibration).

The route-optimizer ranks venues on ``est_slippage_bps`` and ``prior_fill_rate``
— but those are *guesses* until they come from realized fills. This module turns
the audit-grade record into per-venue calibration: for each filled leg we have a
realized slippage (``slippage_usd`` over notional) and, joined from the originating
opportunity, the slippage the model *predicted* (``slippage_estimate_bps``). The
gap between them is the signal the optimizer needs — a venue whose realized
slippage routinely beats the model is being under-weighted, and vice-versa.

Like the rest of AI Phase A: a thin BQ query runner around pure, unit-tested
aggregation, over data already written (``arb_trading.trades`` +
``arb_trading.opportunities``) — no new Pub/Sub fact or table required.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_PROJECT = os.environ.get("GCP_PROJECT_ID")


def _bq_client():
    from google.cloud import bigquery

    return bigquery.Client(project=_PROJECT)


def leg_slippage_bps(size: float, fill_price: float, slippage_usd: float) -> float:
    """Realized slippage of one filled leg, in bps of notional.

    Pure. ``notional = size * fill_price``; a zero/degenerate notional yields
    0.0 (no basis to express slippage against) rather than dividing by zero.
    """
    notional = size * fill_price
    if notional <= 0:
        return 0.0
    return slippage_usd / notional * 10_000.0


def summarize_route_quality(rows) -> dict:
    """Aggregate joined (leg + modeled-slippage) rows into per-venue calibration.

    Each input row is one filled leg: ``exchange``, ``size``, ``fill_price``,
    ``slippage_usd`` (realized) and ``slippage_estimate_bps`` (what the model
    predicted for the parent opportunity). Returns ``{venue: metrics}`` plus an
    ``_overall`` rollup. Pure — no BigQuery — so the math is unit-tested directly.

    Per-venue metrics:
      - ``n_fills``                 number of legs filled on the venue
      - ``total_notional_usd``      sum of size*fill_price
      - ``avg_realized_slippage_bps`` notional-weighted realized slippage
      - ``avg_modeled_slippage_bps``  mean predicted slippage
      - ``slippage_gap_bps``          realized − modeled (positive = worse than modeled)
    """
    by_venue: dict[str, dict] = {}
    for r in rows:
        venue = r["exchange"]
        notional = r["size"] * r["fill_price"]
        realized_bps = leg_slippage_bps(r["size"], r["fill_price"], r["slippage_usd"])
        agg = by_venue.setdefault(
            venue, {"n_fills": 0, "total_notional_usd": 0.0,
                    "_realized_weighted": 0.0, "_modeled_sum": 0.0}
        )
        agg["n_fills"] += 1
        agg["total_notional_usd"] += notional
        agg["_realized_weighted"] += realized_bps * notional
        agg["_modeled_sum"] += r.get("slippage_estimate_bps") or 0.0

    venues: dict[str, dict] = {}
    for venue, agg in by_venue.items():
        notional = agg["total_notional_usd"]
        realized = (agg["_realized_weighted"] / notional) if notional > 0 else 0.0
        modeled = agg["_modeled_sum"] / agg["n_fills"]
        venues[venue] = {
            "n_fills": agg["n_fills"],
            "total_notional_usd": round(notional, 2),
            "avg_realized_slippage_bps": round(realized, 2),
            "avg_modeled_slippage_bps": round(modeled, 2),
            "slippage_gap_bps": round(realized - modeled, 2),
        }

    total_fills = sum(v["n_fills"] for v in venues.values())
    worst = max(venues.items(), key=lambda kv: kv[1]["slippage_gap_bps"], default=(None, None))
    return {
        "venues": dict(sorted(venues.items())),
        "_overall": {
            "venues_covered": len(venues),
            "n_fills": total_fills,
            "worst_calibrated_venue": worst[0],
        },
    }


def route_quality(start_date: str, end_date: str) -> dict:
    """Per-venue execution-quality calibration for live fills in [start, end)."""
    if not _PROJECT:
        raise RuntimeError("GCP_PROJECT_ID unset")

    query = """
    SELECT
      leg.exchange         AS exchange,
      leg.size             AS size,
      leg.fill_price       AS fill_price,
      leg.slippage_usd     AS slippage_usd,
      o.slippage_estimate_bps AS slippage_estimate_bps
    FROM `{project}.arb_trading.trades` AS t,
      UNNEST(t.legs) AS leg
    LEFT JOIN `{project}.arb_trading.opportunities` AS o
      ON o.opportunity_id = t.opportunity_id
    WHERE t.trade_type = 'live'
      AND DATE(t.opened_at) >= @start_date
      AND DATE(t.opened_at) < @end_date
    """.format(project=_PROJECT)

    from google.cloud import bigquery

    job = _bq_client().query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            ]
        ),
    )
    return summarize_route_quality(job.result())
