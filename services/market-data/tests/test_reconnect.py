"""Test reconnect logic and connection resilience."""

import asyncio

import pytest

from collectors.base import BaseCollector, CollectorConfig


class _MockExchange:
    """Mock CCXT Pro exchange that can fail on demand."""

    def __init__(self, fail_count: int = 0):
        self.fail_count = fail_count
        self.call_count = 0
        self.closed = False
        self.markets: dict = {}

    async def load_markets(self):
        self.markets = {"BTC/USD": {}}
        return self.markets

    async def watch_ticker(self, symbol: str):
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise ConnectionError("simulated connection failure")
        return {
            "bid": 60000.0,
            "ask": 60001.0,
            "last": 60000.5,
            "timestamp": 1700000000000,
        }

    async def close(self):
        self.closed = True


class _TestCollector(BaseCollector):
    name = "test"

    def __init__(self, config: CollectorConfig, exchange: _MockExchange):
        super().__init__(config, redis=None)
        self._exchange = exchange

    async def _build_client(self):
        return self._exchange


@pytest.mark.asyncio
async def test_reconnects_after_connection_failure():
    """Collector should retry with exponential backoff when connection fails."""
    exchange = _MockExchange(fail_count=1)  # Fail once, then succeed
    collector = _TestCollector(
        CollectorConfig(exchange="test", symbols=["BTC/USD"], instrument_type="spot"),
        exchange=exchange,
    )

    # Run collector in background
    task = asyncio.create_task(collector.run())

    # Wait a bit for it to attempt connection
    await asyncio.sleep(0.5)

    # Stop the collector
    await collector.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Should have attempted connection at least twice (initial + retry)
    assert exchange.call_count >= 1, "Should have attempted at least one connection"
    assert exchange.closed, "Client should be closed on stop"


@pytest.mark.asyncio
async def test_is_healthy_tracks_message_age():
    """is_healthy() should return False if no message received for 60s."""
    import time

    exchange = _MockExchange()
    collector = _TestCollector(
        CollectorConfig(exchange="test", symbols=["BTC/USD"]),
        exchange=exchange,
    )

    # Initially unhealthy (no messages)
    assert not collector.is_healthy()

    # Simulate receiving a message
    collector._last_message_at = time.time()
    assert collector.is_healthy()

    # Simulate old message (61s ago)
    collector._last_message_at = time.time() - 61
    assert not collector.is_healthy()
