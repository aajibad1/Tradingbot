"""IRS Form 8949 compatible trade export PLUS ordinary-income funding export.

Two tax surfaces:

  * **Form 8949** (capital gains): each closed live ``Trade`` is a
    disposition. Columns:
      (a) Description                  e.g. "1.50000000 BTC"
      (b) Date acquired                MM/DD/YYYY
      (c) Date sold or disposed of     MM/DD/YYYY
      (d) Proceeds (USD)               sale price - sell-side fees
      (e) Cost or other basis (USD)    buy price + buy-side fees
      (f) Code(s)                      'W' = wash sale; we leave blank
      (g) Amount of adjustment         leave blank
      (h) Gain or (loss)               (d) - (e)

  * **Schedule 1 ordinary income** (funding): every funding payment
    received is ordinary income in the year it accrued. The funding
    export bins ``Trade.funding_collected_usd`` by closed_at year and
    emits one row per (asset, exchange-pair) summary.

Open positions are excluded — they appear once they close. The export
pulls from BigQuery so it reflects the audit-grade record, not in-memory
state.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import date
from io import StringIO

logger = logging.getLogger(__name__)


_PROJECT = os.environ.get("GCP_PROJECT_ID")


def _bq_client():
    from google.cloud import bigquery

    return bigquery.Client(project=_PROJECT)


def export_year(tax_year: int) -> str:
    """Return a CSV string with one row per closed trade in `tax_year`.

    Only closed `live` trades are included — paper trades are not taxable.
    """
    if not _PROJECT:
        raise RuntimeError("GCP_PROJECT_ID unset")

    query = """
    SELECT
      trade_id,
      asset,
      opened_at,
      closed_at,
      gross_pnl_usd,
      net_pnl_usd,
      total_fees_usd,
      legs
    FROM `{project}.arb_trading.trades`
    WHERE trade_type = 'live'
      AND status = 'closed'
      AND EXTRACT(YEAR FROM closed_at) = @tax_year
    ORDER BY closed_at ASC
    """.format(project=_PROJECT)

    from google.cloud import bigquery

    job = _bq_client().query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("tax_year", "INT64", tax_year),
            ]
        ),
    )

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Description",
            "Date acquired",
            "Date sold or disposed of",
            "Proceeds (USD)",
            "Cost basis (USD)",
            "Codes",
            "Adjustment",
            "Gain or (loss)",
            "Trade ID",
        ]
    )

    for row in job.result():
        description, proceeds, cost_basis = _form_8949_fields(row)
        writer.writerow(
            [
                description,
                _fmt_date(row["opened_at"]),
                _fmt_date(row["closed_at"]),
                f"{proceeds:.2f}",
                f"{cost_basis:.2f}",
                "",
                "",
                f"{(proceeds - cost_basis):.2f}",
                row["trade_id"],
            ]
        )

    return buf.getvalue()


def export_funding_income_year(tax_year: int) -> str:
    """Return a CSV string with funding-income rows for ``tax_year``.

    Funding payments are taxed as ordinary income (Schedule 1, "Other
    income"), not capital gains. Each row is one closed live trade's net
    funding receipts; aggregate downstream in your tax software.
    """
    if not _PROJECT:
        raise RuntimeError("GCP_PROJECT_ID unset")

    query = """
    SELECT
      trade_id,
      asset,
      long_exchange,
      short_exchange,
      funding_collected_usd,
      opened_at,
      closed_at
    FROM `{project}.arb_trading.trades`
    WHERE trade_type = 'live'
      AND status = 'closed'
      AND EXTRACT(YEAR FROM closed_at) = @tax_year
      AND funding_collected_usd != 0
    ORDER BY closed_at ASC
    """.format(project=_PROJECT)

    from google.cloud import bigquery

    job = _bq_client().query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("tax_year", "INT64", tax_year),
            ]
        ),
    )

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Trade ID",
            "Asset",
            "Long exchange",
            "Short exchange",
            "Date opened",
            "Date closed",
            "Funding income (USD)",
            "Income type",
        ]
    )

    for row in job.result():
        writer.writerow(
            [
                row["trade_id"],
                row["asset"],
                row["long_exchange"],
                row["short_exchange"],
                _fmt_date(row["opened_at"]),
                _fmt_date(row["closed_at"]),
                f"{float(row['funding_collected_usd']):.2f}",
                "funding_payment_ordinary_income",
            ]
        )

    return buf.getvalue()


def _form_8949_fields(row) -> tuple[str, float, float]:
    """Compute Form 8949 description + proceeds + cost basis from a trade row.

    Multi-leg arb trades net the legs by side. The 'description' picks the
    largest filled quantity from the buy side (the position we 'acquired').
    """
    legs = row["legs"] or []
    buy_legs = [leg for leg in legs if leg["side"] == "buy"]
    sell_legs = [leg for leg in legs if leg["side"] == "sell"]

    cost_basis = sum(leg["size"] * leg["fill_price"] + leg["fee_usd"] for leg in buy_legs)
    proceeds = sum(leg["size"] * leg["fill_price"] - leg["fee_usd"] for leg in sell_legs)

    if buy_legs:
        biggest = max(buy_legs, key=lambda leg: leg["size"])
        description = f"{biggest['size']:.8f} {biggest['asset']}"
    else:
        description = f"{row['asset']} arb trade"
    return description, float(proceeds), float(cost_basis)


def _fmt_date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime("%m/%d/%Y")
    return value.strftime("%m/%d/%Y")
