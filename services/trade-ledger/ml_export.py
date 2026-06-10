"""ML training-set export (AI Phase A — closing the label loop).

``risk_decisions`` captures the decision-time *features* + the deterministic
verdict. The realized *label* lives in ``trades`` and arrives later, after the
trade opens and closes. This module joins the two into labelled training rows:

  features (from arb_ml.risk_decisions) + outcome label (from arb_trading.trades)

The join is a LEFT JOIN on ``opportunity_id``: an approved decision that has
executed and closed gets a realized ``net_pnl_usd`` (the regression/classification
target); a rejected decision (or one not yet filled) has no trade row and is
exported with ``was_executed=false`` and a null PnL — the counterfactual negative
a learned ranker needs. Because the trade is asynchronous, this is a batch read
(a BQ join), NOT a write-time join — mirroring the ``tax_export`` pattern: a thin
query runner around a pure, unit-tested row-labelling function.
"""

from __future__ import annotations

import csv
import logging
import os
from io import StringIO

logger = logging.getLogger(__name__)

_PROJECT = os.environ.get("GCP_PROJECT_ID")

# Stable training-row column order (features first, then the realized labels).
_COLUMNS = [
    "decision_id",
    "opportunity_id",
    "tenant_id",
    "strategy",
    "asset",
    "directional",
    "net_edge_bps",
    "gross_spread_bps",
    "trading_fees_bps",
    "slippage_estimate_bps",
    "funding_rate_annualized_pct",
    "confidence_score",
    "recommended_size_usd",
    "approved",
    "advisory_probability",
    "decided_at",
    # --- realized labels (joined from trades; null when not executed) ---
    "was_executed",
    "realized_net_pnl_usd",
    "realized_gross_pnl_usd",
    "label_profitable",
    "trade_status",
    "closed_at",
]


def _bq_client():
    from google.cloud import bigquery

    return bigquery.Client(project=_PROJECT)


def label_training_row(row) -> dict:
    """Map one joined (decision LEFT JOIN trade) BigQuery row to a training row.

    Pure: a ``dict``/``Row``-like in, a flat ``dict`` out — so the label logic is
    unit-tested without BigQuery. ``net_pnl_usd`` is None when the decision never
    produced a (joined) trade; ``label_profitable`` is None in that case (unknown,
    not False — a non-executed decision has no realized outcome to learn from).
    """
    net_pnl = row.get("net_pnl_usd")
    gross_pnl = row.get("gross_pnl_usd")
    trade_status = row.get("trade_status")
    was_executed = trade_status is not None
    label_profitable = (net_pnl > 0) if (was_executed and net_pnl is not None) else None
    return {
        "decision_id": row["decision_id"],
        "opportunity_id": row["opportunity_id"],
        "tenant_id": row["tenant_id"],
        "strategy": row["strategy"],
        "asset": row["asset"],
        "directional": bool(row["directional"]),
        "net_edge_bps": row["net_edge_bps"],
        "gross_spread_bps": row["gross_spread_bps"],
        "trading_fees_bps": row["trading_fees_bps"],
        "slippage_estimate_bps": row["slippage_estimate_bps"],
        "funding_rate_annualized_pct": row["funding_rate_annualized_pct"],
        "confidence_score": row["confidence_score"],
        "recommended_size_usd": row["recommended_size_usd"],
        "approved": bool(row["approved"]),
        "advisory_probability": row.get("advisory_probability"),
        "decided_at": row["decided_at"],
        "was_executed": was_executed,
        "realized_net_pnl_usd": net_pnl,
        "realized_gross_pnl_usd": gross_pnl,
        "label_profitable": label_profitable,
        "trade_status": trade_status,
        "closed_at": row.get("closed_at"),
    }


def fetch_training_rows(start_date: str, end_date: str) -> list[dict]:
    """Labelled training rows for decisions made in [start_date, end_date).

    Dates are ISO ``YYYY-MM-DD`` (partition pruning on ``decided_at``). Only
    ``live`` trades supply realized labels — paper fills are excluded from the
    join so the label reflects real money (paper PnL is simulator output, not a
    ground-truth outcome). Each row is the output of :func:`label_training_row`.
    """
    if not _PROJECT:
        raise RuntimeError("GCP_PROJECT_ID unset")

    query = """
    SELECT
      d.decision_id,
      d.opportunity_id,
      d.tenant_id,
      d.strategy,
      d.asset,
      d.directional,
      d.net_edge_bps,
      d.gross_spread_bps,
      d.trading_fees_bps,
      d.slippage_estimate_bps,
      d.funding_rate_annualized_pct,
      d.confidence_score,
      d.recommended_size_usd,
      d.approved,
      d.advisory_probability,
      d.decided_at,
      t.status      AS trade_status,
      t.net_pnl_usd,
      t.gross_pnl_usd,
      t.closed_at
    FROM `{project}.arb_ml.risk_decisions` AS d
    LEFT JOIN `{project}.arb_trading.trades` AS t
      ON t.opportunity_id = d.opportunity_id
     AND t.trade_type = 'live'
    WHERE DATE(d.decided_at) >= @start_date
      AND DATE(d.decided_at) < @end_date
    ORDER BY d.decided_at ASC
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
    return [label_training_row(row) for row in job.result()]


def export_training_rows(start_date: str, end_date: str) -> str:
    """CSV rendering of :func:`fetch_training_rows` (stable ``_COLUMNS`` order)."""
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=_COLUMNS)
    writer.writeheader()
    for row in fetch_training_rows(start_date, end_date):
        writer.writerow(row)
    return buf.getvalue()


def score_advisory(rows) -> dict:
    """Measure the advisory ranker against realized outcomes (AI Phase B seed).

    The risk-engine records the opportunity-ranker's ``advisory_probability`` at
    decision time; the label loop later attaches the realized ``label_profitable``.
    This compares the two over EXECUTED decisions that carry both — the only rows
    with a ground-truth outcome AND a prediction to grade. Pure: a list of training
    rows in, a metrics dict out.

    Metrics:
      - ``n``            graded rows (executed, has advisory_probability + label)
      - ``base_rate``    realized win rate (the no-skill baseline)
      - ``hit_rate``     accuracy treating p>=0.5 as "predict profitable"
      - ``precision``    win rate among advisory-positive (p>=0.5) calls
      - ``lift``         precision / base_rate  (>1 means the model beats chance)
      - ``brier``        mean (p - y)^2 calibration error (lower is better)
    """
    graded = [
        r for r in rows
        if r.get("was_executed")
        and r.get("advisory_probability") is not None
        and r.get("label_profitable") is not None
    ]
    n = len(graded)
    if n == 0:
        return {"n": 0, "base_rate": None, "hit_rate": None,
                "precision": None, "lift": None, "brier": None}

    wins = sum(1 for r in graded if r["label_profitable"])
    base_rate = wins / n
    hits = sum(1 for r in graded
               if (r["advisory_probability"] >= 0.5) == bool(r["label_profitable"]))
    hit_rate = hits / n
    positives = [r for r in graded if r["advisory_probability"] >= 0.5]
    precision = (sum(1 for r in positives if r["label_profitable"]) / len(positives)
                 if positives else None)
    lift = (precision / base_rate) if (precision is not None and base_rate > 0) else None
    brier = sum((r["advisory_probability"] - (1.0 if r["label_profitable"] else 0.0)) ** 2
                for r in graded) / n
    return {
        "n": n,
        "base_rate": round(base_rate, 4),
        "hit_rate": round(hit_rate, 4),
        "precision": round(precision, 4) if precision is not None else None,
        "lift": round(lift, 4) if lift is not None else None,
        "brier": round(brier, 4),
    }
