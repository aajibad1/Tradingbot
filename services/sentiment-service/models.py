"""Internal models for the sentiment-service.

``SentimentSignal`` is the typed aggregate emitted by the refresh cycle. The
risk-engine never reads this model directly — it reads scalar values from
the ``sentiment:*`` Redis namespace written by ``writer.py``. We keep the
model here (rather than in shared/) because no other service deserializes
it; this avoids a cross-service contract for a single producer/consumer.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SentimentSignal(BaseModel):
    """One aggregated sentiment snapshot from the sentiment-service."""

    observed_at: datetime = Field(description="When the aggregation ran")

    # --- Fear & Greed (Alternative.me) -------------------------------------
    fear_greed_index: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Alternative.me F&G index (0=extreme fear, 100=extreme greed)",
    )
    fear_greed_classification: str | None = Field(
        default=None,
        description="Human-readable F&G band, e.g. 'Extreme Fear'",
    )

    # --- Perplexity Sonar --------------------------------------------------
    perplexity_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Perplexity sentiment score, -1.0 bearish .. +1.0 bullish",
    )
    perplexity_summary: str | None = Field(
        default=None,
        description="One-paragraph rationale returned alongside the score",
    )

    # --- CryptoPanic ------------------------------------------------------
    cryptopanic_panic_score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Derived panic score from CryptoPanic over 24h",
    )
    cryptopanic_articles_24h: int | None = Field(
        default=None,
        ge=0,
        description="Total panic-tagged articles in the last 24h",
    )

    # --- Aggregation result -----------------------------------------------
    is_bearish: bool = Field(
        default=False,
        description=(
            "True iff any tunable threshold tripped. The risk-engine gate "
            "uses this as its primary input."
        ),
    )
    block_reasons: list[str] = Field(
        default_factory=list,
        description="One human-readable reason per tripped threshold",
    )

    # --- Health ------------------------------------------------------------
    sources_ok: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-source fetch success: fear_greed / perplexity / cryptopanic",
    )


class RefreshResponse(BaseModel):
    """Response body for POST /sentiment/refresh."""

    signal: SentimentSignal
    wrote_redis: bool
    redis_ttl_s: int


class HealthResponse(BaseModel):
    status: str
    last_refresh_at: datetime | None = None
    last_signal: SentimentSignal | None = None
    sources_configured: dict[str, bool] = Field(default_factory=dict)
