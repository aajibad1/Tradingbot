"""Endpoint tests (providers + redis stubbed)."""

import pytest
from fastapi.testclient import TestClient

import main


class _Stub:
    def __init__(self, name, data=None, fail=False):
        self.name, self.data, self.fail = name, data or {}, fail

    def fetch(self, symbols=None):
        if self.fail:
            raise RuntimeError("down")
        return dict(self.data)


_FULL = {"ZAR": 18.5, "NGN": 1600.0, "KES": 129.0}
_FB = {"ZAR": 18.6, "NGN": 1750.0, "KES": 130.0}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "_build_providers", lambda: (_Stub("oer", _FULL), _Stub("erh", _FB)))
    monkeypatch.setattr(main, "_redis", lambda: None)
    return TestClient(main.app)


def test_healthz(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_rates_returns_multirate(client) -> None:
    body = client.get("/rates").json()
    assert body["rates"]["ZAR"] == 18.5
    assert body["ngn"]["official"] == 1600.0
    assert body["ngn"]["parallel"] == 1750.0


def test_convert_endpoint(client) -> None:
    body = client.get("/convert", params={"amount_usd": 10, "currency": "ZAR"}).json()
    assert body["amount_local"] == 185.0


def test_convert_ngn_p2p_type(client) -> None:
    # no p2p rate available → unavailable type falls back to official
    body = client.get("/convert", params={"amount_usd": 1, "currency": "NGN", "ngn_rate_type": "official"}).json()
    assert body["amount_local"] == 1600.0


def test_convert_unknown_currency_400(client) -> None:
    assert client.get("/convert", params={"amount_usd": 1, "currency": "GBP"}).status_code == 400


def test_rate_endpoint_is_unit(client) -> None:
    body = client.get("/rate", params={"currency": "KES"}).json()
    assert body["amount_usd"] == 1.0 and body["rate"] == 129.0


def test_rates_503_when_all_providers_down(monkeypatch) -> None:
    monkeypatch.setattr(main, "_build_providers", lambda: (_Stub("a", fail=True), _Stub("b", fail=True)))
    monkeypatch.setattr(main, "_redis", lambda: None)
    assert TestClient(main.app).get("/rates").status_code == 503
