"""Tests for the ML training-set export (AI Phase A label loop)."""

from __future__ import annotations

import csv
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from ml_export import (
    _COLUMNS,
    export_training_rows,
    label_training_row,
    score_advisory,
)


def _decision_features(**over) -> dict:
    base = {
        "decision_id": "o1:rd", "opportunity_id": "o1", "tenant_id": "t1",
        "strategy": "funding_rate_arb", "asset": "BTC", "directional": False,
        "net_edge_bps": 60.0, "gross_spread_bps": 0.0, "trading_fees_bps": 31.0,
        "slippage_estimate_bps": 4.0, "funding_rate_annualized_pct": 15.0,
        "confidence_score": 0.7, "recommended_size_usd": 10_000.0, "approved": True,
        "advisory_probability": 0.8, "decided_at": "2026-01-01T00:00:00Z",
        # join columns (default: no matching trade)
        "trade_status": None, "net_pnl_usd": None, "gross_pnl_usd": None, "closed_at": None,
    }
    base.update(over)
    return base


def test_executed_winning_trade_is_labelled_profitable() -> None:
    row = label_training_row(_decision_features(
        trade_status="closed", net_pnl_usd=42.0, gross_pnl_usd=50.0,
        closed_at="2026-01-02T00:00:00Z",
    ))
    assert row["was_executed"] is True
    assert row["realized_net_pnl_usd"] == 42.0
    assert row["label_profitable"] is True
    assert row["trade_status"] == "closed"


def test_executed_losing_trade_is_labelled_unprofitable() -> None:
    row = label_training_row(_decision_features(
        trade_status="closed", net_pnl_usd=-10.0, gross_pnl_usd=-5.0,
    ))
    assert row["was_executed"] is True
    assert row["label_profitable"] is False


def test_non_executed_decision_has_unknown_label() -> None:
    # A rejected (or unfilled) decision: kept as a counterfactual negative, but
    # the realized label is UNKNOWN (None), not False.
    row = label_training_row(_decision_features(approved=False))
    assert row["was_executed"] is False
    assert row["realized_net_pnl_usd"] is None
    assert row["label_profitable"] is None
    assert row["approved"] is False


def test_label_row_keys_match_csv_columns() -> None:
    # The flat training row must contain exactly the declared CSV columns.
    assert set(label_training_row(_decision_features()).keys()) == set(_COLUMNS)


def test_export_raises_without_gcp_project() -> None:
    with patch("ml_export._PROJECT", None), pytest.raises(RuntimeError):
        export_training_rows("2026-01-01", "2026-02-01")


def _labelled(p: float | None, profitable: bool | None, executed: bool = True) -> dict:
    """A training row as label_training_row would produce, for scoring tests."""
    return {"was_executed": executed, "advisory_probability": p, "label_profitable": profitable}


def test_score_advisory_empty_when_nothing_graded() -> None:
    # Rejected (unexecuted) rows have no realized label → not graded.
    sc = score_advisory([_labelled(0.9, None, executed=False)])
    assert sc["n"] == 0 and sc["base_rate"] is None and sc["brier"] is None


def test_score_advisory_perfect_predictor() -> None:
    rows = [_labelled(1.0, True), _labelled(0.0, False),
            _labelled(0.9, True), _labelled(0.1, False)]
    sc = score_advisory(rows)
    assert sc["n"] == 4
    assert sc["base_rate"] == 0.5
    assert sc["hit_rate"] == 1.0
    assert sc["precision"] == 1.0
    assert sc["lift"] == 2.0          # precision 1.0 / base 0.5
    assert sc["brier"] < 0.05         # near-perfect calibration


def test_score_advisory_ignores_rows_without_prediction_or_label() -> None:
    rows = [_labelled(None, True), _labelled(0.8, None), _labelled(0.8, True)]
    sc = score_advisory(rows)
    assert sc["n"] == 1               # only the fully-populated row counts


def test_score_advisory_coin_flip_has_unit_lift() -> None:
    # Predictor always says 0.6 ("profitable"); half actually win → no skill.
    rows = [_labelled(0.6, True), _labelled(0.6, False)]
    sc = score_advisory(rows)
    assert sc["precision"] == 0.5 and sc["base_rate"] == 0.5
    assert sc["lift"] == 1.0


@patch("ml_export._bq_client")
def test_export_training_rows_emits_csv(mock_bq_client) -> None:
    mock_job = MagicMock()
    mock_job.result.return_value = [
        _decision_features(),  # rejected/unexecuted
        _decision_features(decision_id="o2:rd", opportunity_id="o2",
                           trade_status="closed", net_pnl_usd=42.0, gross_pnl_usd=50.0),
    ]
    mock_client = MagicMock()
    mock_client.query.return_value = mock_job
    mock_bq_client.return_value = mock_client

    with patch("ml_export._PROJECT", "proj-x"):
        out = export_training_rows("2026-01-01", "2026-02-01")

    reader = list(csv.DictReader(StringIO(out)))
    assert len(reader) == 2
    assert reader[0]["was_executed"] == "False"
    assert reader[1]["realized_net_pnl_usd"] == "42.0"
    assert reader[1]["label_profitable"] == "True"
    # query was parameterized on the date window (no string interpolation of dates)
    _, kwargs = mock_client.query.call_args
    params = {p.name for p in kwargs["job_config"].query_parameters}
    assert params == {"start_date", "end_date"}
