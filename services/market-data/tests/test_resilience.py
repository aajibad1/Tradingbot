"""Tier-3 resilience: one bad symbol must not take down the venue, and an
unparseable symbol must not crash the watcher."""

import asyncio

import pytest

from collectors.base import BaseCollector, CollectorConfig


class _PerSymbolMock:
    """watch_ticker raises for BAD/USD, returns ticks for everything else."""

    def __init__(self):
        self.calls: dict[str, int] = {}
        self.closed = False

    async def watch_ticker(self, symbol: str):
        self.calls[symbol] = self.calls.get(symbol, 0) + 1
        if symbol == "BAD/USD":
            raise ValueError("simulated bad symbol")
        await asyncio.sleep(0.01)
        return {"bid": 60000.0, "ask": 60001.0, "last": 60000.5, "timestamp": 1700000000000}

    async def close(self):
        self.closed = True


class _Collector(BaseCollector):
    name = "test"

    def __init__(self, config, exchange):
        super().__init__(config, redis=None)
        self._exchange = exchange

    async def _build_client(self):
        return self._exchange


@pytest.mark.asyncio
async def test_one_bad_symbol_does_not_cancel_siblings():
    ex = _PerSymbolMock()
    collector = _Collector(
        CollectorConfig(exchange="test", symbols=["BTC/USD", "BAD/USD"], instrument_type="spot"),
        exchange=ex,
    )
    task = asyncio.create_task(collector.run())
    await asyncio.sleep(0.3)
    await collector.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # The good symbol kept producing messages even though BAD/USD was failing.
    assert ex.calls.get("BTC/USD", 0) > 1, "good symbol should have ticked repeatedly"
    assert collector._last_message_at > 0, "a healthy message should have arrived"


def test_unparseable_symbol_is_skipped_not_crashed():
    # 'BTCUSD' has no '/' or '-' and binance.us isn't hyperliquid → normalize raises.
    collector = _Collector(
        CollectorConfig(exchange="binance.us", symbols=["BTCUSD"], instrument_type="spot"),
        exchange=_PerSymbolMock(),
    )
    tick = collector._to_exchange_tick("BTCUSD", {"bid": 60000.0, "ask": 60001.0})
    assert tick is None  # skipped, not an exception
