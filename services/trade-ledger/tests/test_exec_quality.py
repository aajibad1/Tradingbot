"""Tests for execution-quality (route calibration) instrumentation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from exec_quality import leg_slippage_bps, route_quality, summarize_route_quality


def _leg(exchange: str, size: float, fill_price: float, slippage_usd: float,
         modeled_bps: float | None) -> dict:
    return {"exchange": exchange, "size": size, "fill_price": fill_price,
            "slippage_usd": slippage_usd, "slippage_estimate_bps": modeled_bps}


def test_leg_slippage_bps_basic() -> None:
    # $10 slippage on $100k notional = 1 bps.
    assert leg_slippage_bps(size=1.0, fill_price=100_000.0, slippage_usd=10.0) == 1.0


def test_leg_slippage_bps_zero_notional_is_safe() -> None:
    assert leg_slippage_bps(size=0.0, fill_price=0.0, slippage_usd=5.0) == 0.0


def test_summarize_groups_by_venue_and_computes_gap() -> None:
    rows = [
        # kraken: $20 slip on $100k = 2 bps realized; modeled 4 bps → -2 gap (better)
        _leg("kraken", 1.0, 100_000.0, 20.0, 4.0),
        # hyperliquid: $90 slip on $90k = 10 bps realized; modeled 4 bps → +6 gap (worse)
        _leg("hyperliquid", 1.0, 90_000.0, 90.0, 4.0),
    ]
    out = summarize_route_quality(rows)
    k = out["venues"]["kraken"]
    h = out["venues"]["hyperliquid"]
    assert k["avg_realized_slippage_bps"] == 2.0
    assert k["slippage_gap_bps"] == -2.0
    assert h["avg_realized_slippage_bps"] == 10.0
    assert h["slippage_gap_bps"] == 6.0
    assert out["_overall"]["worst_calibrated_venue"] == "hyperliquid"
    assert out["_overall"]["n_fills"] == 2
    assert out["_overall"]["venues_covered"] == 2


def test_summarize_notional_weights_realized_slippage() -> None:
    # Same venue, two fills: a tiny 100-bps fill and a large 0-bps fill.
    # Notional-weighting must pull the average toward the large clean fill.
    rows = [
        _leg("kraken", 0.01, 100_000.0, 10.0, 0.0),    # $1k notional, 100 bps
        _leg("kraken", 1.0, 100_000.0, 0.0, 0.0),      # $100k notional, 0 bps
    ]
    out = summarize_route_quality(rows)
    realized = out["venues"]["kraken"]["avg_realized_slippage_bps"]
    # Weighted = (100*1k + 0*100k)/101k ≈ 0.99 bps, NOT the simple mean of 50.
    assert realized < 1.0


def test_summarize_handles_missing_modeled_slippage() -> None:
    out = summarize_route_quality([_leg("kraken", 1.0, 100_000.0, 20.0, None)])
    assert out["venues"]["kraken"]["avg_modeled_slippage_bps"] == 0.0


def test_summarize_empty() -> None:
    out = summarize_route_quality([])
    assert out["venues"] == {}
    assert out["_overall"]["n_fills"] == 0
    assert out["_overall"]["worst_calibrated_venue"] is None


def test_route_quality_raises_without_gcp_project() -> None:
    with patch("exec_quality._PROJECT", None), pytest.raises(RuntimeError):
        route_quality("2026-01-01", "2026-02-01")


@patch("exec_quality._bq_client")
def test_route_quality_queries_and_aggregates(mock_bq_client) -> None:
    mock_job = MagicMock()
    mock_job.result.return_value = [
        _leg("kraken", 1.0, 100_000.0, 20.0, 4.0),
        _leg("hyperliquid", 1.0, 90_000.0, 90.0, 4.0),
    ]
    mock_client = MagicMock()
    mock_client.query.return_value = mock_job
    mock_bq_client.return_value = mock_client

    with patch("exec_quality._PROJECT", "proj-x"):
        out = route_quality("2026-01-01", "2026-02-01")

    assert out["_overall"]["worst_calibrated_venue"] == "hyperliquid"
    _, kwargs = mock_client.query.call_args
    params = {p.name for p in kwargs["job_config"].query_parameters}
    assert params == {"start_date", "end_date"}
