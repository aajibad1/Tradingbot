from datetime import datetime

from pydantic import BaseModel, Field


class MovementSignal(BaseModel):
    """A detected movement signal (hybrid signal plane) — the journaled fact the
    learning layer labels against realized outcomes.

    Emitted by signal-engine to ``Topic.SIGNALS`` on every detection; trade-ledger
    streams them to ``arb_ml.signals``. Without this journal the signal-replay
    service has no substrate: signals were previously in-flight only, so their
    quality (false-positive rate, decay, per-family/per-regime profitability)
    could never be measured after the fact.
    """

    signal_id: str = Field(description="UUID assigned at detection")
    symbol: str = Field(description="Canonical symbol, e.g. 'BTC/USD:PERP'")
    family: str = Field(
        description="'momentum_dislocation' | 'orderbook_imbalance' | 'stat_arb_reversion'"
    )
    direction: str = Field(description="'long' | 'short'")
    gross_edge_bps: float = Field(description="Expected edge at detection")
    confidence: float = Field(ge=0.0, le=1.0)
    expiry_ms: int = Field(ge=0, description="How long the setup was expected to persist")
    regime: str | None = Field(
        default=None, description="Market regime at detection (the gating context)"
    )
    detected_at: datetime
