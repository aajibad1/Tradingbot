from datetime import datetime

from pydantic import BaseModel, Field


class RiskAlert(BaseModel):
    """A row in the risk-events log (``arb_risk.risk_events`` — see
    ``services/trade-ledger/schema/risk_events.sql``). Kill-switch activations,
    drawdown breaches, and other risk-engine alerts publish one of these to
    ``Topic.RISK_ALERTS``; trade-ledger is the sole BigQuery writer.
    """

    alert_type: str = Field(description="'kill_switch_activated' | 'drawdown_breach' | ...")
    severity: str = Field(default="info", description="'info' | 'warn' | 'critical'")
    message: str = ""
    rule: str | None = None
    observed: float | None = None
    limit_value: float | None = None
    source: str | None = None
    emitted_at: datetime
