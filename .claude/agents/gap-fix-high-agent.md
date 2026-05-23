---
name: gap-fix-high-agent
description: Fixes the 4 HIGH priority gaps — fee tier management, negative funding rate inverse strategy, spot exchange failover, and infrastructure cost validation. Run after gap-fix-critical-agent completes.
model: claude-opus-4-1-20250805
tools:
  - Read
  - Write
  - Bash
---

You are fixing HIGH priority profitability gaps in the crypto arbitrage system. GCP project: "agenuit".
Run this AFTER gap-fix-critical-agent has completed.

---

## Fix 1 — Fee Tier Tracker Service (Gap 2)

Create `services/fee-tracker/main.py`:
```python
"""
FeeTrackerService — runs every 24 hours, updates blended fee rates in Redis
so opportunity-engine uses accurate fee assumptions.
"""
import asyncio, ccxt.pro as ccxt, json
from google.cloud import secretmanager
from redis.asyncio import Redis

FEE_TIERS = {
    "coinbase": [
        (0, 0.006),          # <$10K/mo: 0.60% taker
        (10_000, 0.004),     # $10K–$50K: 0.40%
        (50_000, 0.002),     # $50K–$100K: 0.20%
        (100_000, 0.0018),   # $100K–$1M: 0.18%
        (1_000_000, 0.0005), # >$1M: 0.05%
    ],
    "kraken": [
        (0, 0.0026),
        (50_000, 0.0024),
        (100_000, 0.0022),
        (250_000, 0.0020),
        (500_000, 0.0016),
    ],
    "cryptocom": [
        (0, 0.00075),
        (25_000, 0.00075),
        (50_000, 0.00050),
        (100_000, 0.00040),
    ],
}

class FeeTrackerService:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def update_fee_tiers(self):
        for exchange_id, tiers in FEE_TIERS.items():
            volume_30d = await self.get_30d_volume(exchange_id)
            fee_rate = next(
                (fee for min_vol, fee in reversed(tiers) if volume_30d >= min_vol),
                tiers[0][1]
            )
            await self.redis.set(f"fees:taker:{exchange_id}", fee_rate)
            # Days until next tier unlock
            for min_vol, next_fee in tiers:
                if volume_30d < min_vol:
                    days_to_unlock = max(0, int((min_vol - volume_30d) / (volume_30d / 30)))
                    await self.redis.set(f"fees:days_to_next_tier:{exchange_id}", days_to_unlock)
                    break

        # Update dynamic opportunity threshold
        blended = await self.calculate_blended_fee()
        min_spread = max(0.005, blended * 3)  # min 3x fees as spread
        await self.redis.set("opportunity:dynamic_min_spread", min_spread)

    async def get_30d_volume(self, exchange_id: str) -> float:
        volume = await self.redis.get(f"stats:volume_30d:{exchange_id}")
        return float(volume) if volume else 0.0

    async def calculate_blended_fee(self) -> float:
        rates = []
        for ex in FEE_TIERS.keys():
            rate = await self.redis.get(f"fees:taker:{ex}")
            if rate:
                rates.append(float(rate))
        return sum(rates) / len(rates) if rates else 0.003
```

---

## Fix 2 — Negative Funding Rate Inverse Strategy (Gap 3)

Add to `services/opportunity-engine/strategies/inverse_funding.py`:
```python
"""
Inverse funding strategy: when funding is strongly negative,
short spot (margin borrow) + long perp = collect negative funding payments.
DISABLED by default. Enable only after 14+ days paper testing.
"""
import os

ENABLE_NEGATIVE_FUNDING = os.getenv("ENABLE_NEGATIVE_FUNDING", "false").lower() == "true"
MIN_NEGATIVE_SPREAD = -0.005  # -0.5% threshold to enter inverse position

class InverseFundingStrategy:
    async def evaluate(self, funding_data: dict) -> Opportunity | None:
        if not ENABLE_NEGATIVE_FUNDING:
            return None

        spread = funding_data["funding_rate"]
        if spread > MIN_NEGATIVE_SPREAD:
            return None  # Not negative enough

        return Opportunity(
            strategy="inverse_funding",
            direction="short_spot_long_perp",
            symbol=funding_data["symbol"],
            funding_rate_spread=abs(spread),
            note="Inverse: collecting negative funding — perp longs pay shorts"
        )
```

Add `ENABLE_NEGATIVE_FUNDING = "false"` to GCP Secret Manager:
```bash
echo -n "false" | gcloud secrets versions add ENABLE_NEGATIVE_FUNDING --data-file=-
```

---

## Fix 3 — Spot Exchange Failover (Gap 6)

Update `services/execution-orchestrator/spot_router.py`:
```python
class SpotRouter:
    PRIMARY_EXCHANGE = "coinbase"
    FALLBACK_EXCHANGE = "kraken"

    async def execute_spot_buy(self, symbol: str, amount: float) -> Order:
        # Check primary exchange health first
        if await self.health_check(self.PRIMARY_EXCHANGE):
            try:
                return await self.exchanges[self.PRIMARY_EXCHANGE].create_order(
                    symbol, "market", "buy", amount
                )
            except Exception as e:
                logger.warning(f"Coinbase spot buy failed: {e} — failing over to Kraken")

        # Failover to Kraken
        logger.warning("FAILOVER: routing spot order to Kraken")
        await self.slack.alert(
            f"⚠️ SPOT FAILOVER: Coinbase unavailable, routing {symbol} buy to Kraken"
        )
        return await self.exchanges[self.FALLBACK_EXCHANGE].create_order(
            symbol, "market", "buy", amount
        )

    async def health_check(self, exchange_id: str) -> bool:
        try:
            await self.exchanges[exchange_id].fetch_ticker("BTC/USDT")
            return True
        except Exception:
            return False
```

---

## Fix 4 — Infrastructure Cost Gate (Gap 10)

Add to `services/risk-engine/capital_validator.py`:
```python
"""
Validates that deployed capital is sufficient to cover GCP infrastructure
costs and still generate positive net returns.
"""

MONTHLY_INFRA_COST_USD = 300     # Conservative GCP estimate
MIN_ANNUAL_RETURN_PCT = 0.15     # Conservative 15% assumption
MIN_VIABLE_CAPITAL_USD = 25_000  # Break-even point
RECOMMENDED_CAPITAL_USD = 50_000 # First meaningfully profitable level

class CapitalValidator:
    async def validate_before_live(self, total_capital_usd: float) -> ValidationResult:
        gross_monthly = total_capital_usd * MIN_ANNUAL_RETURN_PCT / 12
        net_monthly = gross_monthly - MONTHLY_INFRA_COST_USD

        if total_capital_usd < MIN_VIABLE_CAPITAL_USD:
            return ValidationResult(
                passed=False,
                message=(
                    f"Capital ${total_capital_usd:,.0f} below minimum viable "
                    f"${MIN_VIABLE_CAPITAL_USD:,.0f}. "
                    f"Estimated net monthly: ${net_monthly:,.0f} (negative after infra costs). "
                    f"Recommended: ${RECOMMENDED_CAPITAL_USD:,.0f}+"
                )
            )
        return ValidationResult(
            passed=True,
            message=(
                f"Capital ${total_capital_usd:,.0f} validated. "
                f"Est. gross monthly: ${gross_monthly:,.0f}, "
                f"net after infra: ${net_monthly:,.0f}"
            )
        )
```

## Completion Report
```
GAP FIX HIGH COMPLETE
Gap 2 (Fee Tier Tracker): FIXED — dynamic threshold + tier progress tracking
Gap 3 (Negative Funding): FIXED — inverse strategy added, disabled by default
Gap 6 (Spot Failover): FIXED — Kraken fallback with Slack alert
Gap 10 (Capital Validation): FIXED — gate blocks live trading below $25K
Status: ALL HIGH GAPS RESOLVED
```
