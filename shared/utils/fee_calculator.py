"""Net-edge calculation. Single source of truth for opportunity viability."""

from typing import TypedDict

MIN_VIABLE_NET_EDGE_BPS = 8.0
DEFAULT_SAFETY_BUFFER_BPS = 3.0

# Base-tier taker fees in bps (1 bp = 0.01%). Source: docs/EXCHANGE_FEES.md.
EXCHANGE_TAKER_FEE_BPS: dict[str, float] = {
    "coinbase": 60.0,
    "kraken": 26.0,
    "crypto.com": 7.5,
    "binance.us": 10.0,
    "hyperliquid": 5.0,
}


class CostBreakdown(TypedDict):
    long_fee: float
    short_fee: float
    slippage_long: float
    slippage_short: float
    transfer: float
    borrow: float
    buffer: float


class NetEdgeResult(TypedDict):
    gross_spread_bps: float
    total_cost_bps: float
    net_edge_bps: float
    is_viable: bool
    cost_breakdown: CostBreakdown


def calculate_net_edge(
    gross_spread_bps: float,
    long_exchange_taker_fee_bps: float,
    short_exchange_taker_fee_bps: float,
    slippage_long_bps: float,
    slippage_short_bps: float,
    funding_rate_bps_per_period: float = 0.0,
    transfer_cost_bps: float = 0.0,
    borrow_cost_bps: float = 0.0,
    safety_buffer_bps: float = DEFAULT_SAFETY_BUFFER_BPS,
) -> NetEdgeResult:
    """Compute net edge and full cost breakdown.

    Viable when net_edge_bps > MIN_VIABLE_NET_EDGE_BPS. The caller is also
    expected to check persistence (>= 4h) and confidence separately.
    """
    total_cost_bps = (
        long_exchange_taker_fee_bps
        + short_exchange_taker_fee_bps
        + slippage_long_bps
        + slippage_short_bps
        + transfer_cost_bps
        + borrow_cost_bps
        + safety_buffer_bps
    )
    net_edge_bps = gross_spread_bps + funding_rate_bps_per_period - total_cost_bps

    return {
        "gross_spread_bps": gross_spread_bps,
        "total_cost_bps": total_cost_bps,
        "net_edge_bps": net_edge_bps,
        "is_viable": net_edge_bps > MIN_VIABLE_NET_EDGE_BPS,
        "cost_breakdown": {
            "long_fee": long_exchange_taker_fee_bps,
            "short_fee": short_exchange_taker_fee_bps,
            "slippage_long": slippage_long_bps,
            "slippage_short": slippage_short_bps,
            "transfer": transfer_cost_bps,
            "borrow": borrow_cost_bps,
            "buffer": safety_buffer_bps,
        },
    }


def taker_fee_bps(exchange: str) -> float:
    """Lookup base-tier taker fee. Raises KeyError on unknown exchange — fail loud."""
    return EXCHANGE_TAKER_FEE_BPS[exchange.lower()]
