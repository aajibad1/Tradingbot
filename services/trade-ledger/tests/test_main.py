"""Tests for main.py endpoints and Pub/Sub handling."""

import os
from unittest.mock import patch

from fastapi.testclient import TestClient


@patch.dict(os.environ, {"GCP_PROJECT_ID": ""}, clear=True)
def test_app_starts_without_gcp_project() -> None:
    """Verify the app starts in local mode (no subscribers) when GCP_PROJECT_ID is unset."""
    from main import app

    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@patch.dict(os.environ, {"GCP_PROJECT_ID": ""}, clear=True)
def test_status_endpoint_shows_zero_subscribers_in_local_mode() -> None:
    """Verify /status reports subscribers=0 when GCP_PROJECT_ID is unset."""
    from main import app

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["subscribers"] == 0
        assert data["project_id"] == "unset"


@patch("main.export_year")
@patch.dict(os.environ, {"GCP_PROJECT_ID": ""}, clear=True)
def test_tax_export_endpoint_returns_csv(mock_export_year) -> None:
    """Verify /tax-export endpoint returns CSV file with correct headers."""
    mock_export_year.return_value = "Description,Date acquired,Gain\nBTC,01/01/2026,100.00\n"

    from main import app

    with TestClient(app) as client:
        resp = client.get("/tax-export?year=2026")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert "form_8949_2026.csv" in resp.headers["content-disposition"]
        assert "BTC" in resp.text


@patch("main.export_year")
@patch.dict(os.environ, {"GCP_PROJECT_ID": ""}, clear=True)
def test_tax_export_endpoint_handles_errors(mock_export_year) -> None:
    """Verify /tax-export returns 500 when export_year raises an exception."""
    mock_export_year.side_effect = RuntimeError("BigQuery error")

    from main import app

    with TestClient(app) as client:
        resp = client.get("/tax-export?year=2026")
        assert resp.status_code == 500
        assert "BigQuery error" in resp.json()["detail"]


@patch.dict(os.environ, {"GCP_PROJECT_ID": ""}, clear=True)
def test_tax_export_validates_year_range() -> None:
    """Verify /tax-export rejects years outside 2020-2100."""
    from main import app

    with TestClient(app) as client:
        resp = client.get("/tax-export?year=2019")
        assert resp.status_code == 422  # validation error

        resp = client.get("/tax-export?year=2101")
        assert resp.status_code == 422


@patch("main.export_funding_income_year")
@patch.dict(os.environ, {"GCP_PROJECT_ID": ""}, clear=True)
def test_funding_income_endpoint_returns_csv(mock_export_funding) -> None:
    """Verify /tax-export/funding-income returns CSV file with correct headers."""
    mock_export_funding.return_value = (
        "Trade ID,Asset,Long exchange,Short exchange,Date opened,Date closed,"
        "Funding income (USD),Income type\n"
        "t-1,BTC,kraken,hyperliquid,01/01/2026,01/02/2026,123.45,"
        "funding_payment_ordinary_income\n"
    )
    from main import app

    with TestClient(app) as client:
        resp = client.get("/tax-export/funding-income?year=2026")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert "funding_income_2026.csv" in resp.headers["content-disposition"]
        assert "funding_payment_ordinary_income" in resp.text


@patch("main.export_funding_income_year")
@patch.dict(os.environ, {"GCP_PROJECT_ID": ""}, clear=True)
def test_funding_income_endpoint_handles_errors(mock_export_funding) -> None:
    mock_export_funding.side_effect = RuntimeError("BigQuery error")

    from main import app

    with TestClient(app) as client:
        resp = client.get("/tax-export/funding-income?year=2026")
        assert resp.status_code == 500
        assert "BigQuery error" in resp.json()["detail"]
