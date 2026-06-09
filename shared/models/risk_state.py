from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class RiskRule(str, Enum):
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    PER_TRADE_SIZE_LIMIT = "per_trade_size_limit"
    EXCHANGE_EXPOSURE_LIMIT = "exchange_exposure_limit"
    ASSET_CONCENTRATION_LIMIT = "asset_concentration_limit"
    LEVERAGE_LIMIT = "leverage_limit"
    MIN_NET_EDGE = "min_net_edge"
    API_LATENCY = "api_latency"
    KILL_SWITCH = "kill_switch"
    TENANT_NOT_ELIGIBLE = "tenant_not_eligible"
    DIRECTIONAL_BUDGET = "directional_budget"


class RiskViolation(BaseModel):
    rule: RiskRule
    observed: float
    limit: float
    message: str


class KillSwitchState(BaseModel):
    active: bool = False
    triggered_at: datetime | None = None
    triggered_by: str | None = None
    reason: str | None = None


class RiskState(BaseModel):
    """Snapshot of risk metrics. Persisted in Redis, mirrored to BigQuery for audit."""

    capital_usd: float = Field(gt=0.0)
    daily_pnl_usd: float = 0.0
    daily_loss_pct: float = 0.0
    open_position_count: int = 0
    exchange_exposure_pct: dict[str, float] = Field(default_factory=dict)
    asset_concentration_pct: dict[str, float] = Field(default_factory=dict)
    # Net directional exposure (% of capital) held by the hybrid satellite sleeve.
    # The neutral core does not contribute to this.
    directional_exposure_pct: float = 0.0
    leverage_multiplier: float = 1.0
    kill_switch: KillSwitchState = Field(default_factory=KillSwitchState)
    last_updated: datetime
