-- Every movement signal the signal-engine emits (Topic.SIGNALS) — the learning
-- layer's substrate. signal-replay-service labels these against realized
-- outcomes (joined downstream via the DIRECTIONAL opportunity/trade path) to
-- measure false-positive rate, decay, and per-family/per-regime profitability.
-- Keep forever: labels accrue after the fact, so this is never safe to expire.
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.arb_ml.signals` (
  signal_id       STRING    NOT NULL,  -- assigned at detection; the join key
  symbol          STRING    NOT NULL,  -- canonical, e.g. 'BTC/USD:PERP'
  family          STRING    NOT NULL,  -- momentum_dislocation | orderbook_imbalance | stat_arb_reversion
  direction       STRING    NOT NULL,  -- long | short
  gross_edge_bps  NUMERIC   NOT NULL,  -- expected edge at detection
  confidence      NUMERIC   NOT NULL,
  expiry_ms       INT64     NOT NULL,  -- how long the setup was expected to persist
  regime          STRING,              -- market regime at detection (gating context)
  detected_at     TIMESTAMP NOT NULL,
  ingested_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);
