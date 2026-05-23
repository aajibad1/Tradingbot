"""Internal models for the funding-rate-service.

The canonical FundingRate model lives in shared/. The raw source-shaped
dicts handed back by external APIs are normalized into FundingRate by
`normalizer.py`.
"""

from pydantic import BaseModel, Field


class SourceHealth(BaseModel):
    source: str
    last_success_at: float | None = None
    last_error: str | None = None
    consecutive_failures: int = 0


class CycleResult(BaseModel):
    source: str
    rates_published: int
    duration_ms: int
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    sources: list[SourceHealth] = Field(default_factory=list)
