"""Realistic execution simulator.

Models:
  - tiered taker fees per exchange (lookup table from shared.utils.fee_calculator)
  - slippage via shared.utils.slippage_model (reuses opportunity-engine's calibration)
  - partial-fill risk: 20% probability of 65-95% fill in thin markets
  - latency: 50-150ms execution delay per leg
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.models.trade import TradeLeg
from shared.utils.fee_calculator import taker_fee_bps
from shared.utils.slippage_model import estimate_size_tier_bps

# Partial fills happen ~20% of the time on taker orders in thin markets
_PARTIAL_FILL_PROBABILITY = 0.20
_PARTIAL_FILL_MIN_RATIO = 0.65
_PARTIAL_FILL_MAX_RATIO = 0.95
_LATENCY_MIN_MS = 50
_LATENCY_MAX_MS = 150


@dataclass
class ExecutionResult:
    leg: TradeLeg
    requested_size: float
    fill_ratio: float
    latency_ms: int


def simulate_execution(
    exchange: str,
    asset: str,
    side: str,
    size_usd: float,
    reference_price: float,
    book_depth_usd: float,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> ExecutionResult:
    """Return a single-leg fill with realistic slippage, fees, and partial-fill risk."""
    rng = rng or random.Random()
    now = now or datetime.utcnow()

    fee_bps = taker_fee_bps(exchange)
    slip_bps = estimate_size_tier_bps(size_usd)

    # Slippage direction: buys fill at a worse (higher) price, sells at a worse (lower) price.
    direction = 1 if side == "buy" else -1
    fill_price = reference_price * (1.0 + direction * slip_bps / 10_000.0)

    # Partial-fill sampling — thin markets only (depth < 2x order size).
    fill_ratio = 1.0
    if size_usd >= 0.5 * book_depth_usd and rng.random() < _PARTIAL_FILL_PROBABILITY:
        fill_ratio = rng.uniform(_PARTIAL_FILL_MIN_RATIO, _PARTIAL_FILL_MAX_RATIO)

    filled_notional = size_usd * fill_ratio
    filled_qty = filled_notional / fill_price
    fee_usd = filled_notional * fee_bps / 10_000.0
    slippage_usd = filled_notional * slip_bps / 10_000.0
    latency_ms = rng.randint(_LATENCY_MIN_MS, _LATENCY_MAX_MS)

    leg = TradeLeg(
        exchange=exchange,
        side=side,
        asset=asset,
        size=filled_qty,
        fill_price=fill_price,
        fee_usd=fee_usd,
        slippage_usd=slippage_usd,
        filled_at=now + timedelta(milliseconds=latency_ms),
    )
    return ExecutionResult(
        leg=leg,
        requested_size=size_usd / fill_price,
        fill_ratio=fill_ratio,
        latency_ms=latency_ms,
    )


def new_trade_id() -> str:
    return f"paper-{uuid.uuid4()}"
