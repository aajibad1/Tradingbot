-- Every opportunity detected (executed or not). 7-year retention.
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.arb_trading.opportunities` (
  opportunity_id            STRING    NOT NULL,
  strategy                  STRING    NOT NULL,
  asset                     STRING    NOT NULL,
  long_exchange             STRING    NOT NULL,
  short_exchange            STRING    NOT NULL,
  gross_spread_bps          NUMERIC   NOT NULL,
  trading_fees_bps          NUMERIC   NOT NULL,
  slippage_estimate_bps     NUMERIC   NOT NULL,
  funding_rate_annualized_pct NUMERIC NOT NULL,
  net_edge_bps              NUMERIC   NOT NULL,
  confidence_score          NUMERIC   NOT NULL,
  recommended_size_usd      NUMERIC   NOT NULL,
  min_hold_hours            NUMERIC   NOT NULL,
  detected_at               TIMESTAMP NOT NULL,
  execute                   BOOL      NOT NULL,
  rejection_reason          STRING,
  ingested_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(detected_at)
CLUSTER BY strategy, asset;
