"""FX rate providers — primary (Open Exchange Rates) + free fallback.

Each provider returns ``{currency: usd_to_local}`` for the requested symbols.
``transport`` is injectable so the HTTP path is unit-testable offline.
"""

from __future__ import annotations

import httpx

SYMBOLS = ["ZAR", "NGN", "KES"]


class FxProvider:
    name: str

    def fetch(self, symbols: list[str] = SYMBOLS) -> dict[str, float]:
        raise NotImplementedError


class OpenExchangeRatesProvider(FxProvider):
    """Primary (FX-01). Requires an app_id (free tier OK)."""

    name = "open_exchange_rates"
    _URL = "https://openexchangerates.org/api/latest.json"

    def __init__(self, app_id: str, timeout_s: float = 8.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        if not app_id:
            raise RuntimeError("OPEN_EXCHANGE_RATES_APP_ID unset")
        self.app_id = app_id
        self.timeout_s = timeout_s
        self._transport = transport

    def fetch(self, symbols: list[str] = SYMBOLS) -> dict[str, float]:
        params = {"app_id": self.app_id, "symbols": ",".join(symbols)}
        with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
            resp = client.get(self._URL, params=params)
            resp.raise_for_status()
            rates = resp.json()["rates"]
        return {s: float(rates[s]) for s in symbols if s in rates}


class ExchangeRateHostProvider(FxProvider):
    """Free, keyless fallback (FX-04) — switches in when the primary is down."""

    name = "exchangerate_host"
    _URL = "https://api.exchangerate.host/latest"

    def __init__(self, timeout_s: float = 5.0, transport: httpx.BaseTransport | None = None) -> None:
        self.timeout_s = timeout_s
        self._transport = transport

    def fetch(self, symbols: list[str] = SYMBOLS) -> dict[str, float]:
        params = {"base": "USD", "symbols": ",".join(symbols)}
        with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
            resp = client.get(self._URL, params=params)
            resp.raise_for_status()
            rates = resp.json()["rates"]
        return {s: float(rates[s]) for s in symbols if s in rates}
