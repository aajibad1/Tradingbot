"""shared.health — structured health primitives."""
from __future__ import annotations

from shared.health import (
    Component,
    HealthStatus,
    aggregate,
    build_report,
    run_check,
)


def test_run_check_ok():
    c = run_check("svc", lambda: True)
    assert c.status is HealthStatus.OK and c.latency_ms is not None and c.critical is True


def test_run_check_false_is_down():
    c = run_check("svc", lambda: False)
    assert c.status is HealthStatus.DOWN and "false" in c.detail


def test_run_check_exception_is_down():
    c = run_check("svc", lambda: (_ for _ in ()).throw(ConnectionError("refused")), critical=False)
    assert c.status is HealthStatus.DOWN and "refused" in c.detail and c.critical is False


def test_aggregate_ok():
    assert aggregate([Component("a", HealthStatus.OK), Component("b", HealthStatus.OK)]) is HealthStatus.OK


def test_aggregate_degraded_on_noncritical_down():
    comps = [Component("a", HealthStatus.OK), Component("b", HealthStatus.DOWN, critical=False)]
    assert aggregate(comps) is HealthStatus.DEGRADED


def test_aggregate_down_on_critical_down():
    comps = [Component("a", HealthStatus.OK), Component("b", HealthStatus.DOWN, critical=True)]
    assert aggregate(comps) is HealthStatus.DOWN


def test_build_report_to_dict():
    rep = build_report([Component("a", HealthStatus.OK, latency_ms=12.3)])
    d = rep.to_dict()
    assert d["status"] == "ok"
    assert d["components"][0] == {"name": "a", "status": "ok", "latency_ms": 12.3,
                                  "detail": "", "critical": True}
