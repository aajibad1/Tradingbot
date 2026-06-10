"""Tests for the SLO error-budget + burn-rate evaluator."""

from __future__ import annotations

import pytest

from shared.slo import (
    DEFAULT_SLOS,
    SLOSeverity,
    SLOTarget,
    evaluate_all,
    evaluate_slo,
    worst_severity,
)


def _t(objective: float = 0.999) -> SLOTarget:
    return SLOTarget("test", objective=objective, window_days=30)


def test_objective_must_be_a_proper_fraction() -> None:
    with pytest.raises(ValueError):
        SLOTarget("bad", objective=1.0, window_days=30)
    with pytest.raises(ValueError):
        SLOTarget("bad", objective=0.0, window_days=30)


def test_perfect_window_has_full_budget_and_is_ok() -> None:
    s = evaluate_slo(_t(), good=1000, total=1000)
    assert s.success_rate == 1.0
    assert s.burn_rate == 0.0
    assert s.budget_remaining == 1.0
    assert s.breaching is False
    assert s.severity is SLOSeverity.OK


def test_no_traffic_is_ok_with_null_rate() -> None:
    s = evaluate_slo(_t(), good=0, total=0)
    assert s.success_rate is None
    assert s.breaching is False
    assert s.burn_rate == 0.0
    assert s.severity is SLOSeverity.OK


def test_burn_rate_one_exactly_spends_the_budget() -> None:
    # objective 0.99 → allowed failure 1%. Observed exactly 1% failure → burn 1.0.
    s = evaluate_slo(_t(0.99), good=990, total=1000)
    assert round(s.burn_rate, 6) == 1.0
    assert round(s.budget_remaining, 6) == 0.0
    # success_rate == objective is NOT below it → not breaching.
    assert s.breaching is False
    assert s.severity is SLOSeverity.OK   # 1.0 is not > ticket threshold


def test_moderate_overspend_tickets() -> None:
    # objective 0.99, observed 2% failure → burn 2.0 (> ticket, < page).
    s = evaluate_slo(_t(0.99), good=980, total=1000)
    assert round(s.burn_rate, 4) == 2.0
    assert s.budget_remaining < 0           # over budget
    assert s.breaching is True
    assert s.severity is SLOSeverity.TICKET


def test_fast_burn_pages() -> None:
    # objective 0.999, observed 2% failure → burn 20x (>= 14.4 page threshold).
    s = evaluate_slo(_t(0.999), good=980, total=1000)
    assert s.burn_rate >= 14.4
    assert s.severity is SLOSeverity.PAGE


def test_inputs_are_clamped_defensively() -> None:
    # good > total (a metrics bug) must not crash or produce a >100% rate.
    s = evaluate_slo(_t(), good=1500, total=1000)
    assert s.success_rate == 1.0
    assert s.good == 1000


def test_to_dict_is_serializable_and_rounded() -> None:
    d = evaluate_slo(_t(0.99), good=995, total=1000).to_dict()
    assert d["name"] == "test"
    assert d["severity"] in {"ok", "ticket", "page"}
    assert isinstance(d["burn_rate"], float)


def test_evaluate_all_ignores_unknown_slos_and_uses_catalog() -> None:
    out = evaluate_all({
        "control_plane_availability": (999, 1000),
        "not_a_real_slo": (1, 1),
    })
    assert set(out) == {"control_plane_availability"}
    assert out["control_plane_availability"]["objective"] == 0.999


def test_worst_severity_rolls_up_to_the_most_severe() -> None:
    statuses = {
        "a": {"severity": "ok"},
        "b": {"severity": "ticket"},
        "c": {"severity": "page"},
    }
    assert worst_severity(statuses) is SLOSeverity.PAGE
    assert worst_severity({"a": {"severity": "ok"}}) is SLOSeverity.OK


def test_default_catalog_matches_documented_targets() -> None:
    # Guard the code catalog against drift from docs/SLOS.md.
    assert DEFAULT_SLOS["control_plane_availability"].objective == 0.999
    assert DEFAULT_SLOS["event_processing_lag"].objective == 0.95
    assert all(t.window_days == 30 for t in DEFAULT_SLOS.values())
