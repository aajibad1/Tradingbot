"""Tests for the downsampling/batching tick buffer."""

from __future__ import annotations

from datetime import datetime

from tick_collector import TickBuffer

from shared.models.exchange_tick import ExchangeTick


def _tick(exchange="kraken", symbol="BTC/USD", bid=60_000.0) -> ExchangeTick:
    return ExchangeTick(
        exchange=exchange, symbol=symbol, asset="BTC", bid=bid, ask=bid + 1,
        bid_size=1.0, ask_size=1.0, timestamp=datetime(2026, 1, 1),
    )


def test_downsamples_to_one_per_symbol_per_interval() -> None:
    flushed: list[list[dict]] = []
    buf = TickBuffer(flushed.append, downsample_s=1.0, flush_interval_s=999, max_rows=999)
    buf.add(_tick(), now=100.0)          # kept
    buf.add(_tick(), now=100.5)          # dropped (within 1s)
    buf.add(_tick(), now=100.9)          # dropped
    buf.add(_tick(), now=101.1)          # kept (>1s later)
    assert buf.dropped == 2
    assert len(buf._rows) == 2           # noqa: SLF001


def test_distinct_symbols_not_downsampled_against_each_other() -> None:
    buf = TickBuffer(lambda rows: None, downsample_s=1.0, flush_interval_s=999, max_rows=999)
    buf.add(_tick(symbol="BTC/USD"), now=100.0)
    buf.add(_tick(symbol="ETH/USD"), now=100.1)   # different symbol → kept
    buf.add(_tick(exchange="coinbase", symbol="BTC/USD"), now=100.2)  # different venue → kept
    assert buf.dropped == 0
    assert len(buf._rows) == 3           # noqa: SLF001


def test_flushes_by_size_in_a_single_batch() -> None:
    flushed: list[list[dict]] = []
    buf = TickBuffer(flushed.append, downsample_s=0.0, flush_interval_s=999, max_rows=3)
    for i in range(3):
        buf.add(_tick(bid=60_000.0 + i), now=100.0 + i)
    assert len(flushed) == 1 and len(flushed[0]) == 3   # one batched insert
    assert buf.written == 3
    assert buf._rows == []                # noqa: SLF001


def test_flush_failure_is_swallowed_and_clears_buffer() -> None:
    def boom(_rows):
        raise RuntimeError("bq down")
    buf = TickBuffer(boom, downsample_s=0.0, flush_interval_s=999, max_rows=1)
    buf.add(_tick(), now=1.0)             # triggers flush → raises internally, swallowed
    assert buf.written == 0
    assert buf._rows == []                # noqa: SLF001 — dropped, not stuck
