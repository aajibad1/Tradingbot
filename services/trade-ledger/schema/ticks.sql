-- Forward-collected market-data ticks for cross-exchange backtesting.
-- High volume → lives in arb_market_data (90-day table expiration) and is
-- downsampled to ~1 tick/(exchange,symbol)/second by the collector. Not
-- audit-critical: best-effort writes, lossy-tolerant.
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.arb_market_data.ticks` (
  exchange         STRING    NOT NULL,
  symbol           STRING    NOT NULL,  -- canonical, e.g. 'BTC/USD' or 'BTC/USD:PERP'
  asset            STRING    NOT NULL,
  bid              NUMERIC   NOT NULL,
  ask              NUMERIC   NOT NULL,
  bid_size         NUMERIC,
  ask_size         NUMERIC,
  last             NUMERIC,
  instrument_type  STRING,              -- 'spot' | 'perp'
  observed_at      TIMESTAMP NOT NULL,
  ingested_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(observed_at)
CLUSTER BY exchange, symbol;
