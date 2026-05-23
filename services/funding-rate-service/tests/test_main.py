"""Tests for main.py — source building and healthz endpoint."""

import os
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_build_sources_with_no_credentials() -> None:
    """_build_sources should return empty list when no API keys are configured."""
    from main import _build_sources

    with patch.dict(os.environ, {}, clear=True):
        with patch("sources.coinglass.get_secret", return_value=None):
            with patch("sources.arbitrage_scanner.get_secret", return_value=None):
                sources = _build_sources()
                assert sources == []


def test_build_sources_with_ccxt_only() -> None:
    """_build_sources should initialize CCXTFundingSource when CCXT_FUNDING_EXCHANGES is set."""
    from main import _build_sources

    with patch.dict(os.environ, {"CCXT_FUNDING_EXCHANGES": "binance,kraken"}, clear=True):
        with patch("sources.coinglass.get_secret", return_value=None):
            with patch("sources.arbitrage_scanner.get_secret", return_value=None):
                sources = _build_sources()
                assert len(sources) == 1
                assert sources[0].name == "ccxt"
                assert sources[0].exchanges == ["binance", "kraken"]


def test_healthz_endpoint_returns_ok() -> None:
    """GET /healthz returns 200 with status field."""
    from main import app

    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "sources" in data
    # Status will be 'degraded' when no sources, or 'ok' when sources exist
    assert data["status"] in ("ok", "degraded")
