"""Connector contracts — the seam that makes every venue pluggable.

A connector (CCXT Pro collector, native exchange adapter, on-chain provider,
Africa rail) implements these Protocols and normalizes to the internal event
schema, so the rest of the platform never depends on a specific vendor. See
docs/CONNECTOR_ARCHITECTURE.md.

Two Protocols:
  * MarketDataAdapter — every connector (streams normalized market data + health).
  * TradingAdapter    — only venues we execute on (place orders, read balances).

Protocols (not base classes) so existing collectors conform structurally without
inheritance changes; runtime_checkable enables isinstance() contract assertions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Region/market-type vocabularies the normalizer tags events with.
REGIONS = ("global", "africa")
MARKET_TYPES = ("cex", "dex", "rail", "fx")


@dataclass
class VenueHealth:
    venue: str
    healthy: bool
    latency_ms: int | None = None
    detail: str = ""


@runtime_checkable
class MarketDataAdapter(Protocol):
    """Streams normalized market data for one venue and reports its health.

    Implementations own connection lifecycle, reconnect/backoff, sequence
    handling, and normalization — callers only see the internal schema.
    """
    venue: str
    region: str        # one of REGIONS
    market_type: str   # one of MARKET_TYPES

    async def run(self) -> None: ...
    async def stop(self) -> None: ...
    def health(self) -> VenueHealth: ...


@runtime_checkable
class TradingAdapter(Protocol):
    """Order placement + balances for venues we execute on (managed-custody creds)."""
    venue: str

    async def place(self, order: dict) -> dict: ...
    async def balances(self) -> list[dict]: ...
