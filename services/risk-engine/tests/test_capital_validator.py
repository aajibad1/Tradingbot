"""Tests for the capital-validation rule and the gated /kill-switch/reset.

Capital validation is purely informational on /evaluate (it never blocks an
opportunity). The only side-effect is on /kill-switch/reset, where we
refuse to reset when capital is below the minimum viable level — that's
the safety net that stops an operator from re-enabling trading into a
money-losing posture.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rules.capital_validator import validate_capital


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_validate_capital_below_minimum_fails() -> None:
    result = validate_capital(10_000.0)
    assert result.passed is False
    assert result.capital_usd == 10_000.0
    assert "below minimum" in result.message.lower()


def test_validate_capital_at_minimum_passes() -> None:
    result = validate_capital(25_000.0)
    assert result.passed is True
    assert result.expected_monthly_gross_usd == pytest.approx(25_000 * 0.15 / 12.0)


def test_validate_capital_above_recommended_passes() -> None:
    result = validate_capital(100_000.0)
    assert result.passed is True
    assert "validated" in result.message.lower()


def test_capital_validation_endpoint(client, fake_redis) -> None:
    # conftest seeds capital_usd = 100_000
    resp = client.get("/capital-validation")
    body = resp.json()
    assert resp.status_code == 200
    assert body["passed"] is True
    assert body["capital_usd"] == 100_000.0


def test_reset_blocked_when_undercapitalised(client, fake_redis, monkeypatch) -> None:
    """The kill-switch reset path must refuse to lift the lock when the
    configured capital is below the minimum viable level."""
    monkeypatch.setenv("KILL_SWITCH_RESET_TOKEN", "secret-token")
    fake_redis.set("risk:capital_usd", "5000")  # well below 25K floor
    from rules.kill_switch import trigger_kill_switch

    trigger_kill_switch(fake_redis, "test", "manual")

    resp = client.post(
        "/admin/reset",
        json={"auth_token": "secret-token", "reset_by": "ops"},
    )
    assert resp.status_code == 409
    assert "below minimum" in resp.json()["detail"].lower()
    # Kill switch still active.
    assert fake_redis.get("risk:kill_switch:active") == "1"


def test_reset_allowed_when_capital_above_minimum(client, fake_redis, monkeypatch) -> None:
    """Sanity check: capital >= minimum and correct token → reset succeeds."""
    monkeypatch.setenv("KILL_SWITCH_RESET_TOKEN", "secret-token")
    fake_redis.set("risk:capital_usd", "50000")
    from rules.kill_switch import trigger_kill_switch

    trigger_kill_switch(fake_redis, "test", "manual")

    resp = client.post(
        "/admin/reset",
        json={"auth_token": "secret-token", "reset_by": "ops"},
    )
    assert resp.status_code == 200
    assert resp.json()["active"] is False
