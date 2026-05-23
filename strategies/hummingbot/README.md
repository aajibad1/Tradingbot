# Hummingbot strategy configs

The execution-orchestrator does not spawn Hummingbot processes per trade. It calls a long-running Hummingbot instance over its REST API and asks it to open or close positions. The YAML files in this directory configure the bot **once**, at startup.

| File | Strategy class in opportunity-engine | Cloud Run bot_id |
|---|---|---|
| [funding_rate_arb_config.yml](funding_rate_arb_config.yml) | `FundingRateArbStrategy` | `arb-funding-rate` |
| [spot_perp_basis_config.yml](spot_perp_basis_config.yml) | `SpotPerpBasisStrategy` | `arb-spot-perp-basis` |

## Wiring

1. Hummingbot runs as its own Cloud Run service (or local Docker container during paper trading) and loads the appropriate config at startup.
2. The execution-orchestrator's [`HummingbotClient.submit_order`](../../services/execution-orchestrator/hummingbot_client.py) POSTs to `/bots/{bot_id}/orders` with a payload built from a risk-approved `Opportunity`.
3. The bot opens both legs (within `hedge_window_seconds`), holds per the exit rules, and posts every fill back to the orchestrator via `emit_fills_to_orchestrator: true`.
4. Fill events are republished to `arb-trade-fills` for the trade-ledger to write into BigQuery.

## Risk integration

Both configs include a `risk_kill_switch_check` block. Hummingbot is required to:
- Poll `risk:kill_switch:active` in Redis before submitting any order.
- Cancel all open orders if the key flips to `"1"` while the bot is running.

This is the same Redis key the risk-engine writes — single source of truth across all services. See [services/risk-engine/state.py](../../services/risk-engine/state.py).

## Why no cross-exchange config?

The cross-exchange arb strategy (in [services/opportunity-engine/strategies/cross_exchange.py](../../services/opportunity-engine/strategies/cross_exchange.py)) generates opportunities that the execution-orchestrator routes through Hummingbot — but it doesn't need its own bot config. It reuses the spot legs from `funding_rate_arb_config.yml` (long side on the cheaper venue, short side as a sell order on the dearer venue).

## Phase 2

Live execution is gated behind the 14-day paper validation. Until that passes, both bot configs are deployed but the execution-orchestrator runs in `PAPER_TRADING_MODE=true` and routes approved opportunities to the paper-trader simulator instead of Hummingbot. See the project README for the live-flip procedure.
