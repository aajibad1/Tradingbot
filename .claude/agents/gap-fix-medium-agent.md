---
name: gap-fix-medium-agent
description: Fixes the 4 MEDIUM/LOW gaps — basis trade strategy module, funding rate prediction with EMA, exchange concentration limits, and tax/accounting layer. Run after gap-fix-high-agent completes.
model: claude-sonnet-4-5
tools:
  - Read
  - Write
  - Bash
---

You are fixing MEDIUM priority profitability gaps in the crypto arbitrage system. GCP project: "agenuit".
Run AFTER gap-fix-critical-agent and gap-fix-high-agent have both completed.

---

## Fix 1 — Exchange Concentration Limit (Gap 9)

Add to `services/risk-engine/concentration_guard.py`:
```python
"""
Prevents too much open notional on a single exchange.
Max 40% of total open notional on any one exchange.
"""
MAX_CONCENTRATION_PCT = 0.40

class ConcentrationGuard:
    async def check(self, new_opportunity: Opportunity) -> GuardResult:
        total_open = await self.get_total_open_notional()
        exchange_open = await self.get_exchange_open_notional(
            new_opportunity.perp_exchange
        )
        new_total_on_exchange = exchange_open + new_opportunity.notional_usd

        if total_open == 0:
            return GuardResult(allowed=True)

        concentration = new_total_on_exchange / (total_open + new_opportunity.notional_usd)
        if concentration > MAX_CONCENTRATION_PCT:
            return GuardResult(
                allowed=False,
                reason=(
                    f"Exchange concentration {concentration:.1%} on "
                    f"{new_opportunity.perp_exchange} exceeds max "
                    f"{MAX_CONCENTRATION_PCT:.0%}"
                )
            )
        # Store in Redis for dashboard visibility
        await self.redis.hset(
            "risk:exchange_concentration",
            new_opportunity.perp_exchange,
            concentration
        )
        return GuardResult(allowed=True)

    async def get_exchange_open_notional(self, exchange: str) -> float:
        val = await self.redis.hget("risk:exchange_concentration", exchange)
        return float(val) if val else 0.0
```

---

## Fix 2 — Funding Rate EMA Prediction (Gap 8)

Add to `services/funding-rate-service/predictor.py`:
```python
"""
4-hour EMA of funding rate to predict trend direction.
Only enter positions when rate is elevated AND trending upward.
"""
import numpy as np

class FundingRatePredictor:
    EMA_PERIOD_HOURS = 4
    EMA_ALPHA = 2 / (EMA_PERIOD_HOURS + 1)

    async def is_trending_positive(self, symbol: str, exchange: str) -> bool:
        """Returns True only if funding rate is elevated AND trending upward."""
        history = await self.get_rate_history(symbol, exchange, hours=8)
        if len(history) < 4:
            return False  # Not enough data — skip

        # Calculate EMA
        ema = history[0]
        for rate in history[1:]:
            ema = rate * self.EMA_ALPHA + ema * (1 - self.EMA_ALPHA)

        current_rate = history[-1]
        # Bullish signal: current rate above EMA (momentum positive)
        return current_rate > ema and current_rate > 0

    async def get_rate_history(self, symbol: str, exchange: str,
                                hours: int) -> list[float]:
        key = f"funding:history:{exchange}:{symbol}"
        raw = await self.redis.lrange(key, 0, hours - 1)
        return [float(r) for r in raw] if raw else []

    async def store_rate(self, symbol: str, exchange: str, rate: float):
        key = f"funding:history:{exchange}:{symbol}"
        await self.redis.lpush(key, rate)
        await self.redis.ltrim(key, 0, 47)  # Keep 48 hours of history
```

Update opportunity scorer to use predictor:
```python
# In opportunity_scorer.py evaluate()
trending = await self.predictor.is_trending_positive(
    opportunity.symbol, opportunity.perp_exchange
)
if not trending:
    logger.info(f"SKIPPED: {opportunity.symbol} funding not trending positive")
    return None
```

---

## Fix 3 — Basis Trade Strategy Module (Gap 7, Phase 2)

Create `services/opportunity-engine/strategies/basis_trade.py`:
```python
"""
Basis trade: long spot + short quarterly futures.
Locks in fixed annualized yield when futures trade at premium.
More stable than funding arb — payoff is fixed at expiry.
DISABLED by default. Enable in Phase 2 after funding arb is validated.
"""
import os
from datetime import datetime

ENABLE_BASIS_TRADE = os.getenv("ENABLE_BASIS_TRADE", "false").lower() == "true"
MIN_BASIS_ANNUALIZED = 0.08  # 8% minimum annualized yield to enter

class BasisTradeStrategy:
    async def scan(self, exchange) -> list[BasisOpportunity]:
        if not ENABLE_BASIS_TRADE:
            return []

        opportunities = []
        quarterly_markets = await exchange.fetch_markets()
        quarterly = [m for m in quarterly_markets if m.get("type") == "future"
                     and m.get("expiry")]

        for market in quarterly:
            spot_price = await self.get_spot_price(market["base"], exchange)
            futures_price = (await exchange.fetch_ticker(market["symbol"]))["last"]
            expiry = datetime.fromtimestamp(market["expiry"] / 1000)
            days_to_expiry = (expiry - datetime.now()).days

            if days_to_expiry <= 0:
                continue

            basis_pct = (futures_price - spot_price) / spot_price
            annualized = basis_pct * (365 / days_to_expiry)

            if annualized >= MIN_BASIS_ANNUALIZED:
                opportunities.append(BasisOpportunity(
                    symbol=market["base"],
                    spot_exchange="coinbase",
                    futures_exchange=exchange.id,
                    futures_symbol=market["symbol"],
                    basis_annualized=annualized,
                    days_to_expiry=days_to_expiry,
                    expiry=expiry
                ))
        return sorted(opportunities, key=lambda x: x.basis_annualized, reverse=True)
```

---

## Fix 4 — Tax/Accounting Layer (Gap 11)

Add to `services/trade-ledger/tax_tracker.py`:
```python
"""
Tracks cost basis and funding income for US tax compliance.
Every trade is a taxable event. Every funding payment is ordinary income.
Exports monthly CSV to BigQuery for Koinly/CoinTracker integration.
"""
from dataclasses import dataclass
from datetime import datetime
from google.cloud import bigquery

@dataclass
class TaxLot:
    asset: str
    quantity: float
    acquisition_price: float
    acquisition_date: datetime
    disposal_price: float | None = None
    disposal_date: datetime | None = None
    holding_period_days: int | None = None
    realized_gain_usd: float | None = None
    tax_treatment: str = "short_term"  # all arb trades are short-term

@dataclass
class FundingIncome:
    date: datetime
    symbol: str
    exchange: str
    amount_usd: float
    income_type: str = "funding_payment"  # ordinary income in US

class TaxTracker:
    def __init__(self, bq_client: bigquery.Client):
        self.bq = bq_client

    async def record_trade(self, fill: TradeFill):
        lot = TaxLot(
            asset=fill.base_asset,
            quantity=fill.quantity,
            acquisition_price=fill.entry_price,
            acquisition_date=fill.entry_time,
            disposal_price=fill.exit_price,
            disposal_date=fill.exit_time,
            holding_period_days=(fill.exit_time - fill.entry_time).days,
            realized_gain_usd=(fill.exit_price - fill.entry_price) * fill.quantity,
        )
        await self.insert_to_bq("tax_lots", lot)

    async def record_funding_payment(self, payment: FundingPayment):
        income = FundingIncome(
            date=payment.timestamp,
            symbol=payment.symbol,
            exchange=payment.exchange,
            amount_usd=payment.amount_usd,
        )
        await self.insert_to_bq("funding_income", income)

    async def export_monthly_csv(self, year: int, month: int) -> str:
        query = f"""
        SELECT * FROM arb_trading.tax_lots
        WHERE EXTRACT(YEAR FROM disposal_date) = {year}
          AND EXTRACT(MONTH FROM disposal_date) = {month}
        UNION ALL
        SELECT date, symbol, null, null, null, null, null, amount_usd, income_type
        FROM arb_trading.funding_income
        WHERE EXTRACT(YEAR FROM date) = {year}
          AND EXTRACT(MONTH FROM date) = {month}
        ORDER BY 1
        """
        df = self.bq.query(query).to_dataframe()
        path = f"/tmp/tax_export_{year}_{month:02d}.csv"
        df.to_csv(path, index=False)
        return path
```

## Completion Report
```
GAP FIX MEDIUM COMPLETE
Gap 9 (Concentration Limit): FIXED — 40% max per exchange, tracked in Redis
Gap 8 (Funding EMA): FIXED — 4-hour EMA trend filter added to entry logic
Gap 7 (Basis Trade): ADDED — Phase 2 module, disabled by default
Gap 11 (Tax Layer): FIXED — cost basis + funding income tracking from day 1
Status: ALL 11 GAPS RESOLVED
```
