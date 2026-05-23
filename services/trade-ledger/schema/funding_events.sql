-- Funding rate observations from all sources. 90-day retention.
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.arb_trading.funding_events` (
  exchange              STRING    NOT NULL,
  asset                 STRING    NOT NULL,
  symbol                STRING    NOT NULL,
  rate_per_period       NUMERIC   NOT NULL,
  period_hours          NUMERIC   NOT NULL,
  annualized_pct        NUMERIC   NOT NULL,
  next_funding_time     TIMESTAMP,
  observed_at           TIMESTAMP NOT NULL,
  source                STRING    NOT NULL,  -- 'coinglass' | 'arbitrage_scanner' | 'ccxt'
  ingested_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(observed_at)
CLUSTER BY exchange, asset;
