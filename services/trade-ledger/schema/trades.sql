-- Trade ledger: every paper + live fill. 7-year retention for tax (Form 8949).
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.arb_trading.trades` (
  trade_id            STRING    NOT NULL,
  user_id             STRING    NOT NULL,  -- owning tenant/user
  opportunity_id      STRING    NOT NULL,
  trade_type          STRING    NOT NULL,  -- 'paper' | 'live'
  status              STRING    NOT NULL,  -- 'open' | 'closed' | 'failed'
  asset               STRING    NOT NULL,
  long_exchange       STRING,
  short_exchange      STRING,
  gross_pnl_usd       NUMERIC   NOT NULL,
  net_pnl_usd         NUMERIC   NOT NULL,
  total_fees_usd      NUMERIC   NOT NULL,
  total_slippage_usd  NUMERIC   NOT NULL,
  funding_collected_usd NUMERIC NOT NULL DEFAULT 0,
  opened_at           TIMESTAMP NOT NULL,
  closed_at           TIMESTAMP,
  notes               STRING,
  legs                ARRAY<STRUCT<
    exchange    STRING,
    side        STRING,
    asset       STRING,
    size        NUMERIC,
    fill_price  NUMERIC,
    fee_usd     NUMERIC,
    slippage_usd NUMERIC,
    filled_at   TIMESTAMP
  >>,
  ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(opened_at)
CLUSTER BY user_id, trade_type, asset;
