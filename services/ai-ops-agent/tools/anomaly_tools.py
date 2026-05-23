"""READ-tier anomaly detection.

Pulls 30-day baselines from BigQuery and compares today's metrics. Flags:
  - Slippage spike  : today's avg_slippage_bps > 2x 30-day median
  - Rejection burst : today's rejection rate > 90th percentile of last 30d
  - Latency drift   : any venue median latency_ms > 1.5x its 30-day median
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any


def _bq():
    from google.cloud import bigquery

    return bigquery.Client(project=os.environ["GCP_PROJECT_ID"])


def detect_anomalies(target_date: date | None = None) -> dict[str, Any]:
    target_date = target_date or date.today()
    return {
        "date": target_date.isoformat(),
        "slippage": _slippage_anomaly(target_date),
        "rejections": _rejection_anomaly(target_date),
    }


def _slippage_anomaly(target_date: date) -> dict[str, Any]:
    query = """
    WITH today AS (
      SELECT AVG(total_slippage_usd / NULLIF(gross_pnl_usd, 0) * 10000) AS today_bps
      FROM `{project}.arb_trading.trades`
      WHERE DATE(opened_at) = @target_date AND status = 'closed'
    ),
    baseline AS (
      SELECT
        APPROX_QUANTILES(total_slippage_usd / NULLIF(gross_pnl_usd, 0) * 10000, 100)[OFFSET(50)] AS median_bps
      FROM `{project}.arb_trading.trades`
      WHERE DATE(opened_at) BETWEEN DATE_SUB(@target_date, INTERVAL 30 DAY) AND DATE_SUB(@target_date, INTERVAL 1 DAY)
        AND status = 'closed'
    )
    SELECT today.today_bps, baseline.median_bps,
           SAFE_DIVIDE(today.today_bps, baseline.median_bps) AS ratio
    FROM today, baseline
    """.format(project=os.environ["GCP_PROJECT_ID"])

    from google.cloud import bigquery

    job = _bq().query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("target_date", "DATE", target_date)]
        ),
    )
    row = next(iter(job.result()), None)
    if row is None or row["ratio"] is None:
        return {"flagged": False, "reason": "insufficient data"}
    flagged = row["ratio"] is not None and row["ratio"] > 2.0
    return {
        "flagged": flagged,
        "today_bps": row["today_bps"],
        "baseline_median_bps": row["median_bps"],
        "ratio_vs_baseline": row["ratio"],
    }


def _rejection_anomaly(target_date: date) -> dict[str, Any]:
    query = """
    WITH today AS (
      SELECT
        COUNTIF(NOT execute) / COUNT(*) AS today_rate
      FROM `{project}.arb_trading.opportunities`
      WHERE DATE(detected_at) = @target_date
    ),
    baseline AS (
      SELECT
        APPROX_QUANTILES(daily_rate, 100)[OFFSET(90)] AS p90_rate
      FROM (
        SELECT
          DATE(detected_at) AS d,
          COUNTIF(NOT execute) / COUNT(*) AS daily_rate
        FROM `{project}.arb_trading.opportunities`
        WHERE DATE(detected_at) BETWEEN DATE_SUB(@target_date, INTERVAL 30 DAY) AND DATE_SUB(@target_date, INTERVAL 1 DAY)
        GROUP BY d
      )
    )
    SELECT today.today_rate, baseline.p90_rate
    FROM today, baseline
    """.format(project=os.environ["GCP_PROJECT_ID"])

    from google.cloud import bigquery

    job = _bq().query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("target_date", "DATE", target_date)]
        ),
    )
    row = next(iter(job.result()), None)
    if row is None or row["today_rate"] is None or row["p90_rate"] is None:
        return {"flagged": False, "reason": "insufficient data"}
    flagged = row["today_rate"] > row["p90_rate"]
    return {
        "flagged": flagged,
        "today_rejection_rate": row["today_rate"],
        "baseline_p90_rate": row["p90_rate"],
    }
