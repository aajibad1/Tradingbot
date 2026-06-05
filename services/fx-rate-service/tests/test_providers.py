"""Provider HTTP parsing (offline via httpx.MockTransport)."""

import httpx
import pytest

from providers import ExchangeRateHostProvider, OpenExchangeRatesProvider


def _transport(payload: dict):
    return httpx.MockTransport(lambda req: httpx.Response(200, json=payload))


def test_open_exchange_rates_parses_symbols() -> None:
    p = OpenExchangeRatesProvider("app-id", transport=_transport(
        {"rates": {"ZAR": 18.5, "NGN": 1600.0, "KES": 129.0, "EUR": 0.9}}))
    rates = p.fetch()
    assert rates == {"ZAR": 18.5, "NGN": 1600.0, "KES": 129.0}  # EUR not requested


def test_open_exchange_rates_requires_app_id() -> None:
    with pytest.raises(RuntimeError):
        OpenExchangeRatesProvider("")


def test_fallback_provider_parses() -> None:
    p = ExchangeRateHostProvider(transport=_transport(
        {"rates": {"ZAR": 18.6, "NGN": 1750.0, "KES": 130.0}}))
    assert p.fetch()["NGN"] == 1750.0


def test_provider_raises_on_http_error() -> None:
    t = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        ExchangeRateHostProvider(transport=t).fetch()
