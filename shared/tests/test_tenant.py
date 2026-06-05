"""Tests for the tenant namespacing primitive."""

import pytest

from shared.tenant import (
    DEFAULT_TENANT,
    PLATFORM_KILL_SWITCH_KEY,
    exchange_secret_id,
    risk_key,
    risk_scan_pattern,
    validate_tenant,
)


def test_default_tenant_uses_legacy_keys() -> None:
    # Backward-compat: the single-tenant deployment's keys are unchanged.
    assert risk_key("capital_usd") == "risk:capital_usd"
    assert risk_key("kill_switch:active", DEFAULT_TENANT) == "risk:kill_switch:active"
    assert risk_scan_pattern("exposure:") == "risk:exposure:*"


def test_per_tenant_keys_are_namespaced() -> None:
    assert risk_key("capital_usd", "u123") == "risk:t:u123:capital_usd"
    assert risk_scan_pattern("exposure:", "u123") == "risk:t:u123:exposure:*"
    # Different tenants never collide.
    assert risk_key("capital_usd", "a") != risk_key("capital_usd", "b")


def test_exchange_secret_id() -> None:
    assert exchange_secret_id("kraken", "key") == "arb-kraken-key"
    assert exchange_secret_id("crypto.com", "secret") == "arb-cryptocom-secret"
    assert exchange_secret_id("kraken", "key", "u123") == "user-u123-kraken-key"


def test_platform_kill_switch_is_global() -> None:
    assert PLATFORM_KILL_SWITCH_KEY == "platform:kill_switch:active"


@pytest.mark.parametrize("bad", ["", "a/b", "a:b", "x" * 65, "tenant space", "../escape"])
def test_validate_tenant_rejects_unsafe(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_tenant(bad)
    # And the unsafe id can't leak into a namespaced key either.
    with pytest.raises(ValueError):
        risk_key("capital_usd", bad)
