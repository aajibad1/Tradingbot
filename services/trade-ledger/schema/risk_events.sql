-- Risk alerts, kill-switch activations, rule violations. Keep forever (audit).
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.arb_risk.risk_events` (
  alert_type     STRING    NOT NULL,  -- 'kill_switch_activated' | 'drawdown_breach' | ...
  severity       STRING    NOT NULL,  -- 'info' | 'warn' | 'critical'
  message        STRING    NOT NULL,
  rule           STRING,
  observed       NUMERIC,
  limit_value    NUMERIC,
  source         STRING,
  emitted_at     TIMESTAMP NOT NULL,
  ingested_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(emitted_at)
CLUSTER BY alert_type, severity;
