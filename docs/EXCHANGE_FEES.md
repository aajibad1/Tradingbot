# Exchange Fees

This document is the human-readable counterpart of `shared/utils/fee_calculator.py`.
The Python module is the **source of truth** for runtime fee calculations:

```python
EXCHANGE_TAKER_FEE_BPS: dict[str, float] = {
    "coinbase":    60.0,   # 0.60%
    "kraken":      26.0,   # 0.26%
    "crypto.com":   7.5,   # 0.075%
    "binance.us":  10.0,   # 0.10%
    "hyperliquid":  5.0,   # 0.05%
}
```

If you update this file, also update `EXCHANGE_TAKER_FEE_BPS` (and vice versa).
The opportunity engine and paper trader both depend on those exact bps numbers.

## Base-tier taker fees (used by `calculate_net_edge`)

| Exchange           | Maker         | Taker         | Notes                                        |
| ------------------ | ------------- | ------------- | -------------------------------------------- |
| Coinbase Advanced  | 0.40%         | 0.60%         | US spot only. **No US perps.** Base tier.    |
| Kraken             | 0.16%         | 0.26%         | Pro fee schedule; XBT normalized to BTC.     |
| Crypto.com         | 0.075%        | 0.075%        | Same maker = taker at base tier.             |
| Binance.US         | 0.10%         | 0.10%         | Spot only for US residents.                  |
| Hyperliquid        | 0.02%         | 0.05%         | DEX perps; funding accrues **hourly**.       |

Fees are quoted in **basis points** internally (1 bp = 0.01% = 0.0001).

## Why these tiers?

The numbers above are the *worst-case* (volume tier 0) taker fees. The system
intentionally assumes no volume discount when sizing opportunities. Higher
real-world tiers create headroom, never deficit. `calculate_net_edge` adds a
3 bp `DEFAULT_SAFETY_BUFFER_BPS` on top of these to absorb mid-tick adverse
moves.

## Funding period

`paper-trader` and `funding-rate-service` honour the funding period per venue:

- **Hyperliquid**: 1 hour (DEX perps)
- **All other CEX perps**: 8 hours

Source: `services/funding-rate-service/normalizer.py#from_ccxt` and
`services/paper-trader/simulator/funding_simulator.py#funding_period_hours`.

## Permission model for API keys

Every secret created by `infra/terraform/modules/secret-manager` is intended to
hold a key issued **without withdrawal permission** at the exchange. Terraform
cannot enforce permission scopes on the venue's side; the operator runbook
must verify this before populating each `gcloud secrets versions add`.

| Secret ID             | Service consumer        | Required venue scopes        |
| --------------------- | ----------------------- | ---------------------------- |
| `COINBASE_API_KEY`    | market-data, execution  | view, trade (no transfer)    |
| `KRAKEN_API_KEY`      | market-data, execution  | query, trade (no withdraw)   |
| `KRAKEN_SECRET`       | market-data, execution  | (paired with above)          |
| `CRYPTOCOM_API_KEY`   | market-data, execution  | read, spot-trade             |
| `BINANCE_US_KEY`      | market-data, execution  | read, spot-trade             |
| `HYPERLIQUID_KEY`     | market-data, execution  | trade (no withdraw)          |

`KILL_SWITCH_RESET_TOKEN`, `SLACK_WEBHOOK_URL`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `COINGLASS_API_KEY`, and `ARBITRAGE_SCANNER_KEY` are
operational secrets only — never exchange credentials.
