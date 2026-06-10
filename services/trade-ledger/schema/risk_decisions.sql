-- Every risk-engine decision (approve AND reject) with its decision-time feature
-- snapshot — the ML training substrate (AI Phase A). The realized label (net PnL,
-- did-it-fill) joins back via opportunity_id against arb_trading.trades. Keep
-- forever: labels accrue over each trade's life, so this is never safe to expire.
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.arb_ml.risk_decisions` (
  decision_id                 STRING    NOT NULL,
  opportunity_id              STRING    NOT NULL,  -- join key to detection + realized trade
  tenant_id                   STRING    NOT NULL,
  strategy                    STRING    NOT NULL,
  asset                       STRING    NOT NULL,
  long_exchange               STRING    NOT NULL,
  short_exchange              STRING    NOT NULL,
  directional                 BOOL      NOT NULL,
  -- decision-time feature snapshot (model inputs)
  net_edge_bps                NUMERIC   NOT NULL,
  gross_spread_bps            NUMERIC   NOT NULL,
  trading_fees_bps            NUMERIC   NOT NULL,
  slippage_estimate_bps       NUMERIC   NOT NULL,
  funding_rate_annualized_pct NUMERIC   NOT NULL,
  confidence_score            NUMERIC   NOT NULL,
  recommended_size_usd        NUMERIC   NOT NULL,
  -- the deterministic verdict
  approved                    BOOL      NOT NULL,
  kill_switch_active          BOOL      NOT NULL,
  violation_rules             ARRAY<STRING>,        -- RiskRule values that fired; empty when approved
  -- advisory annotation (fail-open; may be null)
  advisory_action             STRING,
  advisory_probability        NUMERIC,
  model_version               STRING,
  decided_at                  TIMESTAMP NOT NULL,
  ingested_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(decided_at)
CLUSTER BY strategy, approved;
