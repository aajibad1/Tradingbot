"""Tests for the fee-tier tracker (pure helpers + redis-backed writers)."""

import pytest

from shared.utils import fee_tracker as ft
from shared.utils.fee_calculator import (
    DYNAMIC_MIN_EDGE_REDIS_KEY,
    EXCHANGE_TAKER_FEE_BPS,
    MIN_VIABLE_NET_EDGE_BPS,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get(self, key: str):
        return self.kv.get(key)

    def set(self, key: str, value) -> None:
        self.kv[key] = str(value)


def test_tier0_matches_static_fee_table() -> None:
    for ex, fee in EXCHANGE_TAKER_FEE_BPS.items():
        assert ft.tier_for_volume(ex, 0.0).current_fee_bps == fee


def test_tier_for_volume_picks_band_and_next_hint() -> None:
    look = ft.tier_for_volume("kraken", 120_000.0)
    assert look.current_fee_bps == 22.0  # 100k tier
    assert look.next_tier_min_volume_usd == 250_000.0
    assert look.next_tier_fee_bps == 20.0
    # Top tier → no next.
    top = ft.tier_for_volume("hyperliquid", 9_000_000.0)
    assert top.next_tier_min_volume_usd is None
    with pytest.raises(KeyError):
        ft.tier_for_volume("nasdaq", 0.0)


def test_days_to_next_tier_branches() -> None:
    assert ft.days_to_next_tier(0.0, None) is None       # no next tier
    assert ft.days_to_next_tier(0.0, 50_000.0) is None    # no trajectory
    assert ft.days_to_next_tier(60_000.0, 50_000.0) == 0  # already past
    # 30k over 30 days = 1k/day; need 20k more → 20 days.
    assert ft.days_to_next_tier(30_000.0, 50_000.0) == 20


def test_blended_and_dynamic_min_edge() -> None:
    # Empty input → mean of static table.
    static_mean = sum(EXCHANGE_TAKER_FEE_BPS.values()) / len(EXCHANGE_TAKER_FEE_BPS)
    assert ft.blended_fee_bps({}) == pytest.approx(static_mean)
    assert ft.blended_fee_bps({"kraken": 20.0, "hyperliquid": 4.0}) == pytest.approx(12.0)
    # Low blended fee → floor wins (tighten-only).
    assert ft.dynamic_min_edge_bps(1.0) == MIN_VIABLE_NET_EDGE_BPS
    # High blended fee → candidate wins: 20 * 2 * 3 = 120.
    assert ft.dynamic_min_edge_bps(20.0) == pytest.approx(120.0)


def test_update_redis_tier_and_dynamic_min_edge() -> None:
    r = _FakeRedis()
    ft.update_redis_tier(r, "kraken", 30_000.0)
    assert r.kv[f"{ft.REDIS_FEE_KEY_PREFIX}kraken"] == "26.0"
    assert f"{ft.REDIS_DAYS_TO_NEXT_PREFIX}kraken" in r.kv

    written = ft.update_dynamic_min_edge(r, {"kraken": 20.0, "hyperliquid": 4.0})
    assert float(r.kv[DYNAMIC_MIN_EDGE_REDIS_KEY]) == pytest.approx(written)


def test_update_dynamic_min_edge_reads_redis_fees() -> None:
    r = _FakeRedis()
    r.set(f"{ft.REDIS_FEE_KEY_PREFIX}kraken", "20.0")
    r.set(f"{ft.REDIS_FEE_KEY_PREFIX}hyperliquid", "bad-value")  # ignored
    written = ft.update_dynamic_min_edge(r)  # no explicit fees → reads redis
    assert written >= MIN_VIABLE_NET_EDGE_BPS


def test_refresh_all_end_to_end() -> None:
    r = _FakeRedis()
    r.set(f"{ft.REDIS_VOLUME_PREFIX}kraken", "120000")
    r.set(f"{ft.REDIS_VOLUME_PREFIX}coinbase", "not-a-number")  # → treated as 0
    lookups = ft.refresh_all(r)
    assert set(lookups) == set(ft.FEE_TIERS_BPS)
    assert lookups["kraken"].current_fee_bps == 22.0
    assert DYNAMIC_MIN_EDGE_REDIS_KEY in r.kv
