"""api-metering: Meter aggregation + record/usage/quota/check endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from meter import Meter, current_period


# --- pure Meter ------------------------------------------------------------- #


def test_current_period_is_year_month():
    assert current_period(datetime(2026, 6, 9, tzinfo=timezone.utc)) == "2026-06"


def test_meter_aggregates_by_route_and_total():
    m = Meter()
    m.record("ten_1", "POST /v1/onramp/orders", "2026-06", 2)
    m.record("ten_1", "GET /v1/onramp/orders/{id}", "2026-06", 3)
    u = m.usage("ten_1", "2026-06")
    assert u["total"] == 5
    assert u["by_route"]["POST /v1/onramp/orders"] == 2
    assert u["quota"] is None and u["over_quota"] is False and u["remaining"] is None


def test_meter_quota_and_overage_soft_flag():
    m = Meter()
    m.set_quota("ten_1", 10)
    m.record("ten_1", "r", "2026-06", 8)
    u = m.usage("ten_1", "2026-06")
    assert u["over_quota"] is False and u["remaining"] == 2
    # going over still records (soft overage) but flags it
    over = m.record("ten_1", "r", "2026-06", 5)
    assert over["total"] == 13 and over["over_quota"] is True and over["remaining"] == 0


def test_would_exceed_preflight():
    m = Meter()
    m.set_quota("ten_1", 10)
    m.record("ten_1", "r", "2026-06", 9)
    assert m.would_exceed("ten_1", "2026-06", 1) is False
    assert m.would_exceed("ten_1", "2026-06", 2) is True
    assert m.would_exceed("ten_no_quota", "2026-06", 9999) is False  # unlimited


def test_periods_are_isolated():
    m = Meter()
    m.record("ten_1", "r", "2026-05", 4)
    m.record("ten_1", "r", "2026-06", 1)
    assert m.usage("ten_1", "2026-05")["total"] == 4
    assert m.usage("ten_1", "2026-06")["total"] == 1


# --- endpoints -------------------------------------------------------------- #


@pytest.fixture()
def client():
    main._meter = Meter()  # fresh state per test
    return TestClient(main.app)


def test_record_then_usage(client):
    client.post("/v1/metering/record", json={
        "tenant_id": "ten_1", "route": "POST /v1/onramp/orders", "count": 3, "period": "2026-06",
    })
    u = client.get("/v1/metering/usage", params={"tenant": "ten_1", "period": "2026-06"}).json()
    assert u["total"] == 3 and u["by_route"]["POST /v1/onramp/orders"] == 3


def test_quota_set_get_and_overage_via_endpoints(client):
    client.post("/v1/metering/quota", json={"tenant_id": "ten_1", "monthly_limit": 5})
    assert client.get("/v1/metering/quota", params={"tenant": "ten_1"}).json()["monthly_limit"] == 5
    r = client.post("/v1/metering/record", json={
        "tenant_id": "ten_1", "route": "r", "count": 7, "period": "2026-06",
    })
    assert r.json()["over_quota"] is True


def test_quota_not_set_is_404(client):
    r = client.get("/v1/metering/quota", params={"tenant": "ten_none"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "quota_not_set"


def test_check_preflight_endpoint(client):
    client.post("/v1/metering/quota", json={"tenant_id": "ten_1", "monthly_limit": 10})
    client.post("/v1/metering/record", json={"tenant_id": "ten_1", "route": "r", "count": 9,
                                             "period": "2026-06"})
    c = client.get("/v1/metering/check", params={"tenant": "ten_1", "count": 2, "period": "2026-06"})
    assert c.json()["would_exceed"] is True
