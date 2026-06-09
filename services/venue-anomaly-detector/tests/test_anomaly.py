from __future__ import annotations
from anomaly import Severity, VenueSignals, detect


def test_healthy_venue():
    r = detect(VenueSignals("kraken", quote_staleness_ms=100, spread_bps=5,
                            expected_spread_bps=5, update_rate_ratio=1.0))
    assert r.status is Severity.OK and r.degraded is False and r.anomalies == []


def test_stale_quotes_is_critical():
    r = detect(VenueSignals("kraken", quote_staleness_ms=9000))
    assert r.status is Severity.CRITICAL and r.degraded
    assert any(a.kind == "stale_quotes" for a in r.anomalies)


def test_spread_jump_is_warn():
    r = detect(VenueSignals("kraken", spread_bps=30, expected_spread_bps=5, quote_staleness_ms=100))
    assert r.status is Severity.WARN
    assert any(a.kind == "spread_jump" for a in r.anomalies)


def test_sequence_gaps_critical():
    r = detect(VenueSignals("kraken", sequence_gap_rate=0.2, quote_staleness_ms=100))
    assert r.status is Severity.CRITICAL and any(a.kind == "sequence_gaps" for a in r.anomalies)


def test_update_drop_and_rejection():
    r = detect(VenueSignals("kraken", update_rate_ratio=0.2, rejection_rate=0.5, quote_staleness_ms=100))
    kinds = {a.kind for a in r.anomalies}
    assert "update_rate_drop" in kinds and "high_rejection" in kinds
    assert r.status is Severity.CRITICAL  # rejection is critical


def test_critical_dominates_warn():
    r = detect(VenueSignals("kraken", spread_bps=30, expected_spread_bps=5, sequence_gap_rate=0.2))
    assert r.status is Severity.CRITICAL
