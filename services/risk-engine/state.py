"""Redis-backed risk state.

Keys (single source of truth across services):
  risk:kill_switch:active        bool ("1" / "0")
  risk:kill_switch:metadata      JSON {triggered_at, triggered_by, reason}
  risk:daily_pnl_usd             float
  risk:capital_usd               float
  risk:exposure:<exchange>       float (pct of capital)
  risk:concentration:<asset>     float (pct of capital)
  risk:open_positions            int
  risk:leverage_multiplier       float
  risk:last_updated              ISO-8601 string

Cross-plane (read-only here; WRITTEN by core-api, not risk-engine):
  eligibility:t:<tenant>         JSON trading-eligibility snapshot (control plane)

Read-mostly. The risk-engine is the sole writer of risk:*; everyone else is
read-only. The eligibility:* keys are owned by core-api and consumed here.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import TYPE_CHECKING

from shared.models.risk_state import KillSwitchState, RiskState
from shared.tenant import DEFAULT_TENANT, PLATFORM_KILL_SWITCH_KEY, risk_key

if TYPE_CHECKING:
    from redis import Redis


# Key suffixes (tenant-namespaced via shared.tenant.risk_key).
_SUF_KILL_ACTIVE = "kill_switch:active"
_SUF_KILL_META = "kill_switch:metadata"
_SUF_DAILY_PNL = "daily_pnl_usd"
_SUF_CAPITAL = "capital_usd"
_SUF_OPEN_POSITIONS = "open_positions"
_SUF_LEVERAGE = "leverage_multiplier"
_SUF_DIRECTIONAL = "directional_exposure_pct"  # hybrid satellite sleeve net exposure
_SUF_LAST_UPDATED = "last_updated"
_PFX_EXPOSURE = "exposure:"
_PFX_CONCENTRATION = "concentration:"
# Per-opportunity open-position record (JSON). Lets a CLOSE reverse exactly the
# exposure/concentration a prior OPEN added, so those aggregates stay correct.
_PFX_POSITION = "position:"

# Legacy default-tenant key constants — kept for backward-compatible imports and
# equal to the un-namespaced keys the single-tenant deployment already uses.
KEY_KILL_ACTIVE = risk_key(_SUF_KILL_ACTIVE)
KEY_KILL_META = risk_key(_SUF_KILL_META)
KEY_DAILY_PNL = risk_key(_SUF_DAILY_PNL)
KEY_CAPITAL = risk_key(_SUF_CAPITAL)
KEY_OPEN_POSITIONS = risk_key(_SUF_OPEN_POSITIONS)
KEY_LEVERAGE = risk_key(_SUF_LEVERAGE)
KEY_LAST_UPDATED = risk_key(_SUF_LAST_UPDATED)
PREFIX_EXPOSURE = risk_key(_PFX_EXPOSURE)
PREFIX_CONCENTRATION = risk_key(_PFX_CONCENTRATION)
PREFIX_POSITION = risk_key(_PFX_POSITION)


def get_redis() -> Redis:
    from redis import Redis  # local import to keep test envs lightweight

    return Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def kill_active_key(tenant_id: str = DEFAULT_TENANT) -> str:
    """The tenant's kill-switch flag key (used by rules.kill_switch)."""
    return risk_key(_SUF_KILL_ACTIVE, tenant_id)


# ── Platform-wide kill switch (halts EVERY tenant) ──────────────────────────


def is_platform_kill_switch_active(redis: Redis) -> bool:
    return redis.get(PLATFORM_KILL_SWITCH_KEY) == "1"


def set_platform_kill_switch(redis: Redis, triggered_by: str, reason: str) -> None:
    redis.set(PLATFORM_KILL_SWITCH_KEY, "1")
    redis.set(
        f"{PLATFORM_KILL_SWITCH_KEY}:metadata",
        json.dumps({"triggered_by": triggered_by, "reason": reason,
                    "triggered_at": datetime.utcnow().isoformat()}),
    )


def clear_platform_kill_switch(redis: Redis) -> None:
    redis.delete(PLATFORM_KILL_SWITCH_KEY)
    redis.delete(f"{PLATFORM_KILL_SWITCH_KEY}:metadata")


def _scan_prefix(redis: Redis, prefix: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in redis.scan_iter(match=f"{prefix}*"):
        raw = redis.get(key)
        if raw is None:
            continue
        out[key.removeprefix(prefix)] = float(raw)
    return out


def incr_directional_exposure(redis: Redis, delta_pct: float, tenant_id: str = DEFAULT_TENANT) -> float:
    """Adjust the tenant's directional-sleeve exposure (% of capital) and return
    the new total, clamped at >= 0. Sole writer of risk:directional_exposure_pct."""
    key = risk_key(_SUF_DIRECTIONAL, tenant_id)
    total = float(redis.incrbyfloat(key, delta_pct))
    if total < 0:
        redis.set(key, "0")
        total = 0.0
    return total


def load_state(redis: Redis, tenant_id: str = DEFAULT_TENANT) -> RiskState:
    cap_key = risk_key(_SUF_CAPITAL, tenant_id)
    capital = float(redis.get(cap_key) or 0.0)
    if capital <= 0:
        # Bootstrap capital must be set externally; fail loud rather than
        # silently treating an unconfigured system as valid.
        raise RuntimeError(f"{cap_key} unset — risk-engine cannot evaluate rules")

    daily_pnl = float(redis.get(risk_key(_SUF_DAILY_PNL, tenant_id)) or 0.0)
    return RiskState(
        capital_usd=capital,
        daily_pnl_usd=daily_pnl,
        daily_loss_pct=(-daily_pnl / capital * 100.0) if daily_pnl < 0 else 0.0,
        open_position_count=int(redis.get(risk_key(_SUF_OPEN_POSITIONS, tenant_id)) or 0),
        exchange_exposure_pct=_scan_prefix(redis, risk_key(_PFX_EXPOSURE, tenant_id)),
        asset_concentration_pct=_scan_prefix(redis, risk_key(_PFX_CONCENTRATION, tenant_id)),
        directional_exposure_pct=float(redis.get(risk_key(_SUF_DIRECTIONAL, tenant_id)) or 0.0),
        leverage_multiplier=float(redis.get(risk_key(_SUF_LEVERAGE, tenant_id)) or 1.0),
        kill_switch=load_kill_switch(redis, tenant_id),
        last_updated=datetime.fromisoformat(
            redis.get(risk_key(_SUF_LAST_UPDATED, tenant_id)) or datetime.utcnow().isoformat()
        ),
    )


def load_kill_switch(redis: Redis, tenant_id: str = DEFAULT_TENANT) -> KillSwitchState:
    active = redis.get(risk_key(_SUF_KILL_ACTIVE, tenant_id)) == "1"
    meta_raw = redis.get(risk_key(_SUF_KILL_META, tenant_id))
    if not active or not meta_raw:
        return KillSwitchState(active=active)
    meta = json.loads(meta_raw)
    triggered_at = meta.get("triggered_at")
    return KillSwitchState(
        active=True,
        triggered_at=datetime.fromisoformat(triggered_at) if triggered_at else None,
        triggered_by=meta.get("triggered_by"),
        reason=meta.get("reason"),
    )


def set_kill_switch(
    redis: Redis, triggered_by: str, reason: str, tenant_id: str = DEFAULT_TENANT
) -> KillSwitchState:
    state = KillSwitchState(
        active=True,
        triggered_at=datetime.utcnow(),
        triggered_by=triggered_by,
        reason=reason,
    )
    pipe = redis.pipeline()
    pipe.set(risk_key(_SUF_KILL_ACTIVE, tenant_id), "1")
    pipe.set(
        risk_key(_SUF_KILL_META, tenant_id),
        json.dumps(
            {
                "triggered_at": state.triggered_at.isoformat(),
                "triggered_by": state.triggered_by,
                "reason": state.reason,
            }
        ),
    )
    pipe.execute()
    return state


def clear_kill_switch(redis: Redis, tenant_id: str = DEFAULT_TENANT) -> None:
    pipe = redis.pipeline()
    pipe.delete(risk_key(_SUF_KILL_ACTIVE, tenant_id))
    pipe.delete(risk_key(_SUF_KILL_META, tenant_id))
    pipe.execute()


# ── Mutable risk-state writers ───────────────────────────────────────────────
# These are the ONLY place risk:daily_pnl_usd / risk:open_positions /
# risk:exposure:* / risk:concentration:* are written (the risk-engine is the
# sole writer of risk:* keys). They are driven by trade fills via
# position_tracker.apply_trade so the drawdown guard and position-limit checks
# operate on real state instead of perpetual zeros.


def _touch_last_updated(redis: Redis, tenant_id: str = DEFAULT_TENANT) -> None:
    redis.set(risk_key(_SUF_LAST_UPDATED, tenant_id), datetime.utcnow().isoformat())


def incr_daily_pnl(redis: Redis, amount_usd: float, tenant_id: str = DEFAULT_TENANT) -> float:
    """Add realized PnL to the rolling daily total. Returns the new total."""
    new_total = float(redis.incrbyfloat(risk_key(_SUF_DAILY_PNL, tenant_id), amount_usd))
    _touch_last_updated(redis, tenant_id)
    return new_total


def reset_daily_pnl(redis: Redis, tenant_id: str = DEFAULT_TENANT) -> None:
    """Reset the daily PnL counter — call at the UTC day boundary."""
    redis.set(risk_key(_SUF_DAILY_PNL, tenant_id), 0.0)
    _touch_last_updated(redis, tenant_id)


def record_open_position(
    redis: Redis,
    opportunity_id: str,
    asset: str,
    exposure_pct_by_exchange: dict[str, float],
    concentration_pct: float,
    tenant_id: str = DEFAULT_TENANT,
) -> None:
    """Record an opened position and roll its contribution into the aggregates.

    Idempotent on ``opportunity_id``: a duplicate OPEN for an already-tracked
    position is ignored so a redelivered fill cannot double-count exposure.
    """
    pos_key = risk_key(f"{_PFX_POSITION}{opportunity_id}", tenant_id)
    if redis.get(pos_key) is not None:
        return  # already tracked — at-least-once delivery guard

    record = {
        "opportunity_id": opportunity_id,
        "asset": asset,
        "exposure": exposure_pct_by_exchange,
        "concentration": concentration_pct,
    }
    pipe = redis.pipeline()
    pipe.set(pos_key, json.dumps(record))
    for exchange, pct in exposure_pct_by_exchange.items():
        pipe.incrbyfloat(risk_key(f"{_PFX_EXPOSURE}{exchange}", tenant_id), pct)
    pipe.incrbyfloat(risk_key(f"{_PFX_CONCENTRATION}{asset}", tenant_id), concentration_pct)
    pipe.incr(risk_key(_SUF_OPEN_POSITIONS, tenant_id))
    pipe.set(risk_key(_SUF_LAST_UPDATED, tenant_id), datetime.utcnow().isoformat())
    pipe.execute()


def release_position(redis: Redis, opportunity_id: str, tenant_id: str = DEFAULT_TENANT) -> bool:
    """Reverse a previously-recorded open position. No-op if not tracked.

    Paper trades arrive as a single CLOSED record with no prior OPEN, so this
    simply returns False for them (nothing to release). Returns True when an
    open position was found and unwound.
    """
    pos_key = risk_key(f"{_PFX_POSITION}{opportunity_id}", tenant_id)
    raw = redis.get(pos_key)
    if raw is None:
        return False
    record = json.loads(raw)

    pipe = redis.pipeline()
    for exchange, pct in record.get("exposure", {}).items():
        pipe.incrbyfloat(risk_key(f"{_PFX_EXPOSURE}{exchange}", tenant_id), -float(pct))
    pipe.incrbyfloat(
        risk_key(f"{_PFX_CONCENTRATION}{record['asset']}", tenant_id),
        -float(record.get("concentration", 0.0)),
    )
    pipe.decr(risk_key(_SUF_OPEN_POSITIONS, tenant_id))
    pipe.delete(pos_key)
    pipe.set(risk_key(_SUF_LAST_UPDATED, tenant_id), datetime.utcnow().isoformat())
    pipe.execute()
    _clamp_nonnegative_aggregates(redis, record, tenant_id)
    return True


def _clamp_nonnegative_aggregates(redis: Redis, record: dict, tenant_id: str = DEFAULT_TENANT) -> None:
    """Guard against float drift / over-release leaving small negatives.

    Exposure and open-position counts are physically non-negative; clamp the
    keys this release touched back to >= 0 and drop ~zero exposure keys so
    /state stays clean.
    """
    keys = [risk_key(f"{_PFX_EXPOSURE}{ex}", tenant_id) for ex in record.get("exposure", {})]
    keys.append(risk_key(f"{_PFX_CONCENTRATION}{record['asset']}", tenant_id))
    for key in keys:
        raw = redis.get(key)
        if raw is None:
            continue
        val = float(raw)
        if val <= 1e-9:
            redis.delete(key)
    open_key = risk_key(_SUF_OPEN_POSITIONS, tenant_id)
    if int(redis.get(open_key) or 0) < 0:
        redis.set(open_key, 0)
