---
name: gap-fix-critical-agent
description: Fixes the 3 CRITICAL gaps identified in the profitability research report — MIN_FUNDING_SPREAD threshold, isolated margin enforcement, and real-time order book slippage model. These must be fixed before any live trading.
model: claude-opus-4-1-20250805
tools:
  - Read
  - Write
  - Bash
---

You are fixing CRITICAL profitability gaps in the crypto arbitrage system. GCP project: "agenuit".

## Context
Research identified 3 CRITICAL gaps that will cause the system to lose money or get liquidated if not fixed before going live. Fix all 3 now.

---

## Fix 1 — Raise MIN_FUNDING_SPREAD to 0.5% (Gap 1)

File: `services/opportunity-engine/config.py`

Current (WRONG — fees consume 60-80% of profit at this level):
```python
MIN_FUNDING_SPREAD = 0.002  # 0.2%
```

Fix:
```python
# Minimum gross spread required to cover fees + slippage + leave net edge
# Breakdown: 0.15% fees + 0.1% slippage + 0.25% net edge = 0.5% minimum
MIN_FUNDING_SPREAD = 0.005  # 0.5% — do NOT lower this without fee tier analysis

# Dynamic threshold: after 30 days, adjust based on actual blended fee rate
# Loaded from Redis key: opportunity:dynamic_min_spread (updated by fee-tracker-service)
DYNAMIC_THRESHOLD_ENABLED = True
DYNAMIC_THRESHOLD_REDIS_KEY = "opportunity:dynamic_min_spread"
```

Also update `services/opportunity-engine/opportunity_scorer.py`:
```python
async def is_viable(self, opportunity: Opportunity) -> bool:
    # Check dynamic threshold first, fall back to static
    dynamic_min = await self.redis.get(config.DYNAMIC_THRESHOLD_REDIS_KEY)
    min_spread = float(dynamic_min) if dynamic_min else config.MIN_FUNDING_SPREAD

    gross_spread = opportunity.funding_rate_spread
    estimated_fees = opportunity.entry_fee + opportunity.exit_fee
    estimated_slippage = await self.slippage_model.estimate(opportunity)
    net_spread = gross_spread - estimated_fees - estimated_slippage

    if gross_spread < min_spread:
        logger.info(f"REJECTED: gross spread {gross_spread:.4f} < min {min_spread:.4f}")
        return False

    if net_spread <= 0:
        logger.info(f"REJECTED: net spread {net_spread:.4f} <= 0 after costs")
        return False

    return True
```

---

## Fix 2 — Enforce Isolated Margin on ALL Perp Positions (Gap 4)

File: `services/execution-orchestrator/hummingbot/order_builder.py`

Add to EVERY perp order creation — this is non-negotiable:
```python
class PerpOrderBuilder:
    def build_short(self, symbol: str, amount: float, exchange: str) -> dict:
        return {
            "symbol": symbol,
            "side": "sell",
            "type": "market",
            "amount": amount,
            "params": {
                "marginMode": "isolated",    # CRITICAL: never cross-margin
                "leverage": 1,               # 1x only — no leverage on arb positions
                "reduceOnly": False,
            }
        }

    def build_close(self, symbol: str, amount: float) -> dict:
        return {
            "symbol": symbol,
            "side": "buy",
            "type": "market",
            "amount": amount,
            "params": {
                "marginMode": "isolated",
                "reduceOnly": True,          # safety: only closes, never opens new
            }
        }
```

Add liquidation price monitor to `services/risk-engine/liquidation_monitor.py`:
```python
class LiquidationMonitor:
    ALERT_THRESHOLD_PCT = 0.15  # alert when price within 15% of liquidation

    async def check_all_positions(self):
        positions = await self.redis.hgetall("positions:open")
        for pos_id, pos_data in positions.items():
            pos = json.loads(pos_data)
            liq_price = pos.get("liquidation_price")
            current_price = await self.get_price(pos["symbol"])
            if not liq_price:
                continue
            distance = abs(current_price - liq_price) / current_price
            if distance < self.ALERT_THRESHOLD_PCT:
                await self.slack.alert(
                    f"⚠️ LIQUIDATION WARNING: {pos['symbol']} current={current_price:.2f} "
                    f"liq={liq_price:.2f} distance={distance:.1%}"
                )
                if distance < 0.05:  # 5% = trigger kill switch
                    await self.kill_switch.activate(reason=f"Liquidation imminent: {pos['symbol']}")
```

---

## Fix 3 — Real-Time Order Book Slippage Model (Gap 5)

Create `services/opportunity-engine/slippage_model.py`:
```python
import ccxt.pro as ccxt
from dataclasses import dataclass

@dataclass
class SlippageEstimate:
    estimated_pct: float
    order_book_depth_usd: float
    is_viable: bool
    rejection_reason: str | None = None

class OrderBookSlippageModel:
    """
    Estimates market impact before committing to a trade.
    Uses top 10 order book levels to calculate true slippage.
    """
    MAX_SLIPPAGE_AS_PCT_OF_SPREAD = 0.50  # reject if slippage > 50% of gross spread

    async def estimate(self, symbol: str, order_size_usd: float,
                       exchange, gross_spread: float) -> SlippageEstimate:
        try:
            ob = await exchange.fetch_order_book(symbol, limit=10)
            # Calculate total available liquidity within 1% of mid price
            mid = (ob["bids"][0][0] + ob["asks"][0][0]) / 2
            depth_1pct = sum(
                price * qty for price, qty in ob["bids"]
                if price >= mid * 0.99
            )
            if depth_1pct == 0:
                return SlippageEstimate(
                    estimated_pct=1.0,
                    order_book_depth_usd=0,
                    is_viable=False,
                    rejection_reason="No order book depth available"
                )
            # Market impact formula: slippage scales linearly with order/depth ratio
            slippage_pct = order_size_usd / depth_1pct
            max_allowed = gross_spread * self.MAX_SLIPPAGE_AS_PCT_OF_SPREAD
            is_viable = slippage_pct <= max_allowed
            return SlippageEstimate(
                estimated_pct=slippage_pct,
                order_book_depth_usd=depth_1pct,
                is_viable=is_viable,
                rejection_reason=None if is_viable else
                    f"Slippage {slippage_pct:.3%} > max allowed {max_allowed:.3%}"
            )
        except Exception as e:
            # If order book unavailable, reject the trade — never assume zero slippage
            return SlippageEstimate(
                estimated_pct=1.0, order_book_depth_usd=0,
                is_viable=False,
                rejection_reason=f"Order book fetch failed: {e}"
            )
```

## Completion Report
```
GAP FIX CRITICAL COMPLETE
Gap 1 (MIN_FUNDING_SPREAD): FIXED — raised to 0.5% with dynamic adjustment
Gap 4 (Isolated Margin): FIXED — enforced on all perp orders + liquidation monitor
Gap 5 (Slippage Model): FIXED — real order book depth model, rejects if slippage > 50% of spread
Status: READY FOR PAPER TRADING VALIDATION
```
