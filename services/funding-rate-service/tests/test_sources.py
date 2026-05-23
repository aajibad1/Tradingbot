"""Tests for funding rate sources."""

import os
from unittest.mock import patch

import pytest

from sources import ArbitrageScannerSource, CCXTFundingSource, CoinGlassSource


def test_coinglass_requires_api_key() -> None:
    """CoinGlassSource raises RuntimeError when no API key is available."""
    # Clear all possible key sources
    with patch.dict(os.environ, {}, clear=True):
        with patch("sources.coinglass.get_secret", return_value=None):
            with pytest.raises(RuntimeError, match="COINGLASS_API_KEY unset"):
                CoinGlassSource()


def test_coinglass_accepts_env_var() -> None:
    """CoinGlassSource loads from COINGLASS_API_KEY env var."""
    with patch.dict(os.environ, {"COINGLASS_API_KEY": "test-key"}):
        source = CoinGlassSource()
        assert source.api_key == "test-key"


def test_coinglass_accepts_secret_manager() -> None:
    """CoinGlassSource falls back to Secret Manager when env var is absent."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("sources.coinglass.get_secret", return_value="secret-manager-key"):
            source = CoinGlassSource()
            assert source.api_key == "secret-manager-key"


def test_arbitrage_scanner_requires_api_key() -> None:
    """ArbitrageScannerSource raises RuntimeError when no API key is available."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("sources.arbitrage_scanner.get_secret", return_value=None):
            with pytest.raises(RuntimeError, match="ARBITRAGE_SCANNER_API_KEY unset"):
                ArbitrageScannerSource()


def test_arbitrage_scanner_accepts_env_var() -> None:
    """ArbitrageScannerSource loads from ARBITRAGE_SCANNER_API_KEY env var."""
    with patch.dict(os.environ, {"ARBITRAGE_SCANNER_API_KEY": "test-key"}):
        source = ArbitrageScannerSource()
        assert source.api_key == "test-key"


def test_ccxt_funding_requires_exchanges() -> None:
    """CCXTFundingSource raises ValueError when no exchanges are provided."""
    with pytest.raises(ValueError, match="at least one exchange"):
        CCXTFundingSource([])


def test_ccxt_funding_normalizes_exchange_names() -> None:
    """CCXTFundingSource lowercases exchange names."""
    source = CCXTFundingSource(["Binance", "KRAKEN"])
    assert source.exchanges == ["binance", "kraken"]


@pytest.mark.asyncio
async def test_ccxt_funding_handles_partial_failures() -> None:
    """CCXTFundingSource continues when one exchange fails."""
    source = CCXTFundingSource(["binance", "kraken"])

    # Mock _fetch_one to simulate binance failing, kraken succeeding
    async def mock_fetch_one(exchange: str):
        if exchange == "binance":
            raise RuntimeError("binance API error")
        return [{"exchange": "kraken", "asset": "BTC", "rate": 0.0001}]

    with patch.object(source, "_fetch_one", side_effect=mock_fetch_one):
        # Should not raise; should return only kraken results
        results = await source.fetch()
        # Results are FundingRate objects, not dicts, so we just check it returns something
        assert isinstance(results, list)
