# Crypto Arbitrage System — Architecture

## System Overview

```
                         ┌─────────────────────────────────────────────────┐
                         │                   EXCHANGES                      │
                         │  Coinbase  ·  Kraken  ·  Crypto.com  ·  HL DEX │
                         └──────────────────┬──────────────────────────────┘
                                            │ WebSocket (CCXT Pro)
                         ┌──────────────────▼──────────────────────────────┐
                         │            market-data-service                   │
                         │     Collects ticks + order books → Pub/Sub       │
                         └──────────────────┬──────────────────────────────┘
                                            │
               ┌────────────────────────────┼───────────────────────┐
               │                            │                       │
  ┌────────────▼──────────┐   ┌─────────────▼─────────┐   ┌───────▼──────────────┐
  │  funding-rate-service  │   │  opportunity-engine   │   │   trade-ledger       │
  │  CoinGlass + CCXT     │   │  Score + rank opps    │   │   BigQuery writer    │
  │  → arb-funding-rates  │   │  → arb-opportunities  │   │   Tax export         │
  └────────────┬──────────┘   └─────────────┬─────────┘   └──────────────────────┘
               │                            │
               └──────────┬─────────────────┘
                           │
              ┌────────────▼────────────────────────┐
              │           risk-engine                │
              │  Kill switch · Drawdown guard        │
              │  Exchange health · Position limits   │
              │  Redis state · Circuit breakers      │
              └────────────┬────────────────────────┘
                           │
               ┌───────────┼───────────────────┐
               │           │                   │
  ┌────────────▼──────┐  ┌─▼─────────────┐  ┌─▼──────────────────────┐
  │   paper-trader    │  │  ai-ops-agent  │  │ execution-orchestrator │
  │   Simulate only   │  │  MCP server   │  │ Hummingbot client      │
  │   → Validate PnL  │  │  READ only    │  │ Human approval gate    │
  └───────────────────┘  └───────────────┘  └────────────────────────┘
                                │
                         Claude / Gemini
                         (via MCP protocol)
```

## Data Flow

1. **market-data-service** streams ticks/orderbooks from exchanges via CCXT Pro WebSocket
2. **funding-rate-service** polls funding rates from CoinGlass, ArbitrageScanner, and CCXT every 60s
3. **opportunity-engine** subscribes to both streams, calculates net_edge_bps per opportunity
4. **risk-engine** validates each opportunity against limits before allowing it to flow downstream
5. **paper-trader** simulates execution (fees, slippage, partial fills) for 14+ day validation
6. **trade-ledger** writes every quote, opportunity, trade, and event to BigQuery
7. **ai-ops-agent** reads from BigQuery/Redis to generate daily reports and anomaly alerts

## Risk Controls

| Control | Threshold | Action |
|---------|-----------|--------|
| Daily loss | > 1% of capital | Kill switch triggered |
| Per-trade size | > 2% of capital | Signal blocked |
| Exchange exposure | > 25% per venue | Signal blocked |
| API latency | > 500ms | Exchange paused |
| Net edge | < 8 bps | Opportunity rejected |
| Kill switch | Active | ALL signals blocked |

## AI Agent Permission Model

| Operation | AI Permission | Human Required? |
|-----------|---------------|-----------------|
| Read balances/PnL | ✅ Autonomous | No |
| Generate daily report | ✅ Autonomous | No |
| Detect anomalies | ✅ Autonomous | No |
| Propose risk limit change | 📤 Propose only | Yes — Slack approval |
| Change trade size | 📤 Propose only | Yes — Slack approval |
| Withdraw funds | ❌ Blocked | Never (hardcoded) |
| Increase leverage | ❌ Blocked | Never (hardcoded) |

## Exchange Fee Reference

| Exchange | Taker Fee | Has Perps? | US Legal? |
|----------|-----------|------------|-----------|
| Coinbase Advanced | 0.40–0.60% | No (US) | ✅ Yes |
| Kraken | 0.26% | Yes (Kraken Pro) | ✅ Yes |
| Crypto.com | 0.075% | Yes | ✅ Yes |
| Binance.US | 0.10% | No | ✅ Yes |
| Hyperliquid | 0.05% | Yes (DEX) | ✅ Yes |

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Cloud | GCP (Cloud Run, Pub/Sub, BigQuery, Redis) |
| IaC | Terraform (modular) |
| Exchange APIs | CCXT Pro + native SDKs |
| Execution | Hummingbot |
| AI Ops | MCP server → Claude/Gemini |
| CI/CD | GitHub Actions |
| Dev | Cursor + Warp + GitHub CLI |
