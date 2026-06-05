"""Internal request/response models for the risk-engine HTTP API."""

from pydantic import BaseModel, Field

from shared.models.opportunity import Opportunity
from shared.models.risk_state import RiskViolation
from shared.tenant import DEFAULT_TENANT


class EvaluateRequest(BaseModel):
    opportunity: Opportunity
    tenant_id: str = Field(default=DEFAULT_TENANT, description="User/tenant whose risk state to evaluate against")


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
