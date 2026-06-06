"""Tenant eligibility gate — the execution-plane half of the control-plane seam."""

from __future__ import annotations

import json

from rules.eligibility_gate import check_eligibility
from shared.tenant import eligibility_key


def _snap(redis, tenant, **fields):
    redis.set(eligibility_key(tenant), json.dumps(fields))


def test_default_tenant_is_never_gated(fake_redis):
    # legacy single-tenant deployment — no snapshot, never blocked
    assert check_eligibility(fake_redis, "default") == []


def test_missing_snapshot_is_allowed_by_default(fake_redis):
    # backward compatible: unknown tenant passes unless enforcement is required
    assert check_eligibility(fake_redis, "userA") == []


def test_missing_snapshot_is_blocked_when_required(fake_redis):
    v = check_eligibility(fake_redis, "userA", require=True)
    assert len(v) == 1
    assert v[0].rule.value == "tenant_not_eligible"


def test_not_trading_ready_is_blocked(fake_redis):
    _snap(fake_redis, "userB", trading_ready=False, plan="free")
    v = check_eligibility(fake_redis, "userB")
    assert len(v) == 1
    assert "trading_ready" in v[0].message


def test_trading_ready_passes(fake_redis):
    _snap(fake_redis, "userC", trading_ready=True, plan="pro")
    assert check_eligibility(fake_redis, "userC") == []


def test_unreadable_snapshot_is_blocked(fake_redis):
    fake_redis.set(eligibility_key("userD"), "not-json{")
    v = check_eligibility(fake_redis, "userD")
    assert len(v) == 1
    assert v[0].rule.value == "tenant_not_eligible"
