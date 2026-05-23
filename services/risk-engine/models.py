"""Internal request/response models for the risk-engine HTTP API."""

from pydantic import BaseModel, Field

from shared.models.opportunity import Opportunity
from shared.models.risk_state import RiskViolation


class EvaluateRequest(BaseModel):
    opportunity: Opportunity


class EvaluateResponse(BaseModel):
    approved: bool
    violations: list[RiskViolation] = Field(default_factory=list)
    kill_switch_active: bool = False


class KillSwitchTriggerRequest(BaseModel):
    triggered_by: str = Field(description="User or service that triggered the kill")
    reason: str


class KillSwitchResetRequest(BaseModel):
    auth_token: str = Field(description="Out-of-band shared secret required to re-enable trading")
    reset_by: str
