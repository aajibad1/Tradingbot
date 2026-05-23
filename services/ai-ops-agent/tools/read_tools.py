"""READ-tier tools.

Read directly from Redis (live state) and BigQuery (historical). No mutation,
no Pub/Sub emit. Safe for the AI to call autonomously.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any


def _redis():
    from redis import Redis

    return Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def _bq():
    from google.cloud import bigquery

    return bigquery.Client(project=os.environ["GCP_PROJECT_ID"])


def get_balances() -> dict[str, float]:
    """Return current capital + per-venue exposure as USD."""
    r = _redis()
    capital = float(r.get("risk:capital_usd") or 0.0)
    exposures: dict[str, float] = {}
    for key in r.scan_iter(match="risk:exposure:*"):
        venue = key.removeprefix("risk:exposure:")
        exposures[venue] = float(r.get(key) or 0.0)
    return {"capital_usd": capital, "exposure_pct_by_venue": exposures}


def get_daily_pnl(target_date: date | None = None) -> dict[str, Any]:
    """Return PnL for a date (default: today, both paper + live)."""
    target_date = target_date or date.today()
    query = """
    SELECT
      trade_type,
      COUNT(*) AS trade_count,
      SUM(net_pnl_usd) AS net_pnl_usd,
      SUM(gross_pnl_usd) AS gross_pnl_usd,
      SUM(total_fees_usd) AS fees_usd,
      SUM(funding_collected_usd) AS funding_usd
    FROM `{project}.arb_trading.trades`
    WHERE DATE(opened_at) = @target_date AND status = 'closed'
    GROUP BY trade_type
    """.format(project=os.environ["GCP_PROJECT_ID"])

    from google.cloud import bigquery

    job = _bq().query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("target_date", "DATE", target_date)]
        ),
    )
    rows = {row["trade_type"]: dict(row) for row in job.result()}
    return {"date": target_date.isoformat(), "by_trade_type": rows}


def get_open_positions() -> dict[str, Any]:
    """Return all open trades. Empty list when flat."""
    query = """
    SELECT trade_id, opportunity_id, asset, long_exchange, short_exchange,
           opened_at, gross_pnl_usd, funding_collected_usd
    FROM `{project}.arb_trading.trades`
    WHERE status = 'open'
    ORDER BY opened_at ASC
    """.format(project=os.environ["GCP_PROJECT_ID"])
    job = _bq().query(query)
    return {"positions": [dict(r) for r in job.result()]}


def get_exchange_health() -> dict[str, Any]:
    """Per-exchange API latency (ms) from Redis."""
    r = _redis()
    out: dict[str, float] = {}
    for key in r.scan_iter(match="health:latency:*"):
        venue = key.removeprefix("health:latency:")
        out[venue] = float(r.get(key) or 0.0)
    kill = r.get("risk:kill_switch:active") == "1"
    return {"latency_ms_by_venue": out, "kill_switch_active": kill}


def get_opportunity_feed(limit: int = 25) -> dict[str, Any]:
    """Recent opportunities (executed + rejected). Useful for AI to assess engine output."""
    query = """
    SELECT opportunity_id, strategy, asset, long_exchange, short_exchange,
           net_edge_bps, confidence_score, recommended_size_usd, detected_at,
           execute, rejection_reason
    FROM `{project}.arb_trading.opportunities`
    WHERE detected_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)
    ORDER BY detected_at DESC
    LIMIT @limit
    """.format(project=os.environ["GCP_PROJECT_ID"])
    from google.cloud import bigquery

    job = _bq().query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", limit)]
        ),
    )
    return {"opportunities": [dict(r) for r in job.result()]}
