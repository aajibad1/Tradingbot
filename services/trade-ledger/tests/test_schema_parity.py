"""Lock the model → row-builder → BigQuery-schema contract (M10).

The row builders in ``writer.py`` are intentionally explicit (a model change in
shared/ must not silently reshape a BQ table). The flip side is that the derived
column names (e.g. ``total_fees_usd``, and ``limit`` → ``limit_value``) live
implicitly in writer code, with nothing tying them to the actual schema. A
rename on *either* side — the row builder or the ``schema/*.sql`` DDL — would
previously drift silently and only surface as a failed streaming insert (or a
dashboard query returning nulls) in production.

This test parses the column names out of each ``CREATE TABLE`` and asserts the
matching row builder produces exactly those columns (excluding the
DB-managed ``ingested_at`` default). Either side drifting now fails CI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from writer import (
    audit_log_to_row,
    funding_to_row,
    opportunity_to_row,
    risk_alert_to_row,
    tick_to_row,
    trade_to_row,
)

from shared.models.exchange_tick import ExchangeTick
from shared.models.funding_rate import FundingRate
from shared.models.opportunity import Opportunity, StrategyType
from shared.models.trade import Trade, TradeLeg, TradeStatus, TradeType

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

# Columns BigQuery populates itself — never produced by a row builder.
_DB_MANAGED = {"ingested_at"}


def _top_level_columns(sql: str) -> set[str]:
    """Extract top-level column names from a CREATE TABLE statement.

    Handles nested ``ARRAY<STRUCT<...>>`` (angle-bracket depth) and
    ``DEFAULT CURRENT_TIMESTAMP()`` (paren depth), and strips ``-- comments``.
    """
    # Strip inline + full-line comments first so a paren inside a comment
    # (e.g. the header "(Form 8949)") doesn't get mistaken for the column list.
    sql = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    start = sql.index("(")
    depth = 0
    body_chars: list[str] = []
    for ch in sql[start:]:
        if ch == "(":
            depth += 1
            if depth == 1:
                continue  # skip the outer opening paren itself
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        body_chars.append(ch)
    body = "".join(body_chars)

    # Split on commas that are at the top level (not inside <...> or (...)).
    fields: list[str] = []
    token: list[str] = []
    angle = paren = 0
    for ch in body:
        if ch == "<":
            angle += 1
        elif ch == ">":
            angle -= 1
        elif ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        if ch == "," and angle == 0 and paren == 0:
            fields.append("".join(token))
            token = []
        else:
            token.append(ch)
    if "".join(token).strip():
        fields.append("".join(token))

    columns: set[str] = set()
    for field in fields:
        # Drop comment-only lines, then take the first identifier token.
        cleaned = "\n".join(
            line for line in field.splitlines() if not line.strip().startswith("--")
        ).strip()
        if cleaned:
            columns.add(cleaned.split()[0])
    return columns


def _trade_row() -> dict:
    leg_buy = TradeLeg(exchange="kraken", side="buy", asset="BTC", size=0.1,
                       fill_price=60_000.0, fee_usd=15.0, slippage_usd=1.0,
                       filled_at=datetime(2026, 1, 1))
    leg_sell = TradeLeg(exchange="hyperliquid", side="sell", asset="BTC", size=0.1,
                        fill_price=60_500.0, fee_usd=3.0, slippage_usd=1.0,
                        filled_at=datetime(2026, 1, 1))
    return trade_to_row(Trade(
        id="t1", opportunity_id="o1", type=TradeType.PAPER, legs=[leg_buy, leg_sell],
        gross_pnl_usd=10.0, net_pnl_usd=5.0, status=TradeStatus.CLOSED,
        opened_at=datetime(2026, 1, 1), closed_at=datetime(2026, 1, 1),
    ))


def _opportunity_row() -> dict:
    return opportunity_to_row(Opportunity(
        id="o1", strategy=StrategyType.FUNDING_RATE_ARB, asset="BTC",
        long_exchange="kraken", short_exchange="hyperliquid", gross_spread_bps=0.0,
        trading_fees_bps=31.0, slippage_estimate_bps=4.0, funding_rate_annualized_pct=15.0,
        net_edge_bps=60.0, confidence_score=0.7, recommended_size_usd=10_000.0,
        min_hold_hours=6.0, detected_at=datetime(2026, 1, 1),
    ))


def _funding_row() -> dict:
    return funding_to_row(FundingRate(
        exchange="hyperliquid", asset="BTC", symbol="BTC/USD:PERP",
        rate_per_period=0.0001, period_hours=1.0, annualized_pct=30.0,
        observed_at=datetime(2026, 1, 1), source="ccxt",
    ))


def _risk_event_row() -> dict:
    return risk_alert_to_row({
        "alert_type": "drawdown_breach", "severity": "critical", "message": "x",
        "rule": "max_daily_loss_pct", "observed": 1.5, "limit": 1.0,
        "source": "risk-engine", "emitted_at": "2026-01-01T00:00:00Z",
    })


def _audit_row() -> dict:
    return audit_log_to_row({
        "event_id": "e1", "source": "ai-ops-agent", "event_type": "proposal",
        "actor": "claude", "action": "propose", "resource_type": "limit",
        "resource_id": "x", "metadata": {}, "emitted_at": "2026-01-01T00:00:00Z",
    })


def _tick_row() -> dict:
    return tick_to_row(ExchangeTick(
        exchange="kraken", symbol="BTC/USD", asset="BTC", bid=60_000.0, ask=60_010.0,
        bid_size=1.0, ask_size=1.0, timestamp=datetime(2026, 1, 1),
    ))


_CASES = {
    "trades.sql": _trade_row,
    "opportunities.sql": _opportunity_row,
    "funding_events.sql": _funding_row,
    "risk_events.sql": _risk_event_row,
    "audit_log.sql": _audit_row,
    "ticks.sql": _tick_row,
}


@pytest.mark.parametrize("schema_file,row_builder", list(_CASES.items()))
def test_row_keys_match_schema_columns(schema_file: str, row_builder) -> None:
    schema_cols = _top_level_columns((_SCHEMA_DIR / schema_file).read_text())
    expected = schema_cols - _DB_MANAGED
    actual = set(row_builder().keys())
    assert actual == expected, (
        f"{schema_file}: row builder vs schema drift.\n"
        f"  only in schema: {sorted(expected - actual)}\n"
        f"  only in row:    {sorted(actual - expected)}"
    )


def test_parser_finds_nested_struct_column_once() -> None:
    """Sanity-check the SQL parser handles ARRAY<STRUCT<...>> (the trades.legs case)."""
    cols = _top_level_columns((_SCHEMA_DIR / "trades.sql").read_text())
    assert "legs" in cols
    # Nested struct fields must NOT leak up as top-level columns.
    assert "fill_price" not in cols
    assert "ingested_at" in cols  # present in schema (filtered out at comparison)
