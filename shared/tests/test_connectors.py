"""Connector contract tests."""
from __future__ import annotations

from shared.connectors import MarketDataAdapter, TradingAdapter, VenueHealth


class _Adapter:
    venue = "kraken"
    region = "global"
    market_type = "cex"
    async def run(self): ...
    async def stop(self): ...
    def health(self): return VenueHealth(self.venue, True, 50)


def test_venue_health_fields():
    h = VenueHealth(venue="valr", healthy=False, latency_ms=120, detail="stale")
    assert h.venue == "valr" and h.healthy is False and h.latency_ms == 120


def test_conforming_object_isinstance_market_data_adapter():
    assert isinstance(_Adapter(), MarketDataAdapter)


def test_non_conforming_object_is_not_an_adapter():
    class _NotAdapter:
        venue = "x"
    assert not isinstance(_NotAdapter(), MarketDataAdapter)


def test_trading_adapter_protocol():
    class _T:
        venue = "kraken"
        async def place(self, order): return {}
        async def balances(self): return []
    assert isinstance(_T(), TradingAdapter)
