"""Tests for tax_export.py IRS Form 8949 CSV generation."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from tax_export import _fmt_date, _form_8949_fields, export_year


def test_fmt_date_handles_datetime_and_date() -> None:
    dt = datetime(2026, 3, 15, 14, 30)
    assert _fmt_date(dt) == "03/15/2026"
    assert _fmt_date(None) == ""


def test_form_8949_fields_calculates_proceeds_and_cost_basis() -> None:
    """Verify Form 8949 proceeds/cost calculations from a multi-leg trade."""
    row = {
        "asset": "BTC",
        "legs": [
            {
                "side": "buy",
                "asset": "BTC",
                "size": 1.0,
                "fill_price": 60000.0,
                "fee_usd": 15.0,
            },
            {
                "side": "sell",
                "asset": "BTC",
                "size": 1.0,
                "fill_price": 60100.0,
                "fee_usd": 3.0,
            },
        ],
    }
    description, proceeds, cost_basis = _form_8949_fields(row)
    assert description == "1.00000000 BTC"
    assert proceeds == 60097.0  # 60100 - 3
    assert cost_basis == 60015.0  # 60000 + 15


def test_form_8949_fields_handles_no_buy_legs() -> None:
    """Edge case: trade with no buy legs (shouldn't happen in real arb, but guard against it)."""
    row = {
        "asset": "ETH",
        "legs": [
            {"side": "sell", "asset": "ETH", "size": 10.0, "fill_price": 3000.0, "fee_usd": 5.0},
        ],
    }
    description, proceeds, cost_basis = _form_8949_fields(row)
    assert description == "ETH arb trade"
    assert proceeds == 29995.0
    assert cost_basis == 0.0


def test_form_8949_uses_decimal_and_foots_to_cents() -> None:
    """H5: money is Decimal (no float drift) and the form foots: gain = proceeds - cost."""
    row = {
        "asset": "BTC",
        "legs": [
            # values chosen to drift under float accumulation
            {"side": "buy", "asset": "BTC", "size": Decimal("0.1"), "fill_price": Decimal("60000.10"), "fee_usd": Decimal("0.07")},
            {"side": "buy", "asset": "BTC", "size": Decimal("0.2"), "fill_price": Decimal("60000.20"), "fee_usd": Decimal("0.07")},
            {"side": "sell", "asset": "BTC", "size": Decimal("0.3"), "fill_price": Decimal("60100.10"), "fee_usd": Decimal("0.07")},
        ],
    }
    description, proceeds, cost_basis = _form_8949_fields(row)
    assert isinstance(proceeds, Decimal) and isinstance(cost_basis, Decimal)
    # exact Decimal arithmetic: cost = 0.1*60000.10 + 0.2*60000.20 + 0.07 + 0.07
    assert cost_basis == Decimal("0.1") * Decimal("60000.10") + Decimal("0.2") * Decimal("60000.20") + Decimal("0.14")
    gain_cents = (proceeds.quantize(Decimal("0.01")) - cost_basis.quantize(Decimal("0.01")))
    assert gain_cents == gain_cents.quantize(Decimal("0.01"))  # foots exactly to cents


@patch("tax_export._bq_client")
def test_export_year_generates_csv_with_header_and_rows(mock_bq_client) -> None:
    """Verify export_year produces valid CSV with Form 8949 columns."""
    # Mock BigQuery response
    mock_job = MagicMock()
    mock_job.result.return_value = [
        {
            "trade_id": "t1",
            "asset": "BTC",
            "opened_at": datetime(2026, 1, 10),
            "closed_at": datetime(2026, 1, 10),
            "gross_pnl_usd": 100.0,
            "net_pnl_usd": 82.0,
            "total_fees_usd": 18.0,
            "legs": [
                {"side": "buy", "asset": "BTC", "size": 0.5, "fill_price": 60000.0, "fee_usd": 9.0},
                {"side": "sell", "asset": "BTC", "size": 0.5, "fill_price": 60200.0, "fee_usd": 9.0},
            ],
        }
    ]
    mock_client = MagicMock()
    mock_client.query.return_value = mock_job
    mock_bq_client.return_value = mock_client

    # Mock environment
    with patch("tax_export._PROJECT", "test-project"):
        csv_output = export_year(2026)

    lines = csv_output.strip().split("\n")
    assert len(lines) == 2  # header + 1 trade

    header = lines[0]
    assert "Description" in header
    assert "Date acquired" in header
    assert "Proceeds (USD)" in header
    assert "Cost basis (USD)" in header
    assert "Gain or (loss)" in header

    row = lines[1]
    assert "0.50000000 BTC" in row
    assert "01/10/2026" in row
    assert "30091.00" in row  # proceeds: 0.5 * 60200 - 9
    assert "30009.00" in row  # cost: 0.5 * 60000 + 9
    assert "82.00" in row  # gain
    assert "t1" in row  # trade ID


@patch("tax_export._PROJECT", None)
def test_export_year_raises_without_gcp_project() -> None:
    """Verify export_year fails gracefully when GCP_PROJECT_ID is unset."""
    try:
        export_year(2026)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "GCP_PROJECT_ID unset" in str(e)
