-- System-wide audit log. Keep forever.
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.arb_audit.audit_log` (
  event_id       STRING    NOT NULL,
  source         STRING    NOT NULL,  -- 'risk-engine' | 'ai-ops-agent' | 'execution-orchestrator'
  event_type     STRING    NOT NULL,  -- 'limit_change' | 'kill_switch_reset' | 'trade_approved' | ...
  actor          STRING,               -- 'system' | 'claude@ai-ops' | 'admin@example.com'
  action         STRING,
  resource_type  STRING,
  resource_id    STRING,
  metadata       JSON,
  emitted_at     TIMESTAMP NOT NULL,
  ingested_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(emitted_at)
CLUSTER BY source, event_type;
