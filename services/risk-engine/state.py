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

Read-mostly. The risk-engine is the sole writer; everyone else is read-only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import TYPE_CHECKING

from shared.models.risk_state import KillSwitchState, RiskState

if TYPE_CHECKING:
    from redis import Redis


KEY_KILL_ACTIVE = "risk:kill_switch:active"
KEY_KILL_META = "risk:kill_switch:metadata"
KEY_DAILY_PNL = "risk:daily_pnl_usd"
KEY_CAPITAL = "risk:capital_usd"
KEY_OPEN_POSITIONS = "risk:open_positions"
KEY_LEVERAGE = "risk:leverage_multiplier"
KEY_LAST_UPDATED = "risk:last_updated"
PREFIX_EXPOSURE = "risk:exposure:"
PREFIX_CONCENTRATION = "risk:concentration:"
# Per-opportunity open-position record (JSON). Lets a CLOSE reverse exactly the
# exposure/concentration a prior OPEN added, so those aggregates stay correct
# across the position lifecycle instead of drifting.
PREFIX_POSITION = "risk:position:"


def get_redis() -> Redis:
    from redis import Redis  # local import to keep test envs lightweight

    return Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def _scan_prefix(redis: Redis, prefix: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in redis.scan_iter(match=f"{prefix}*"):
        raw = redis.get(key)
        if raw is None:
            continue
        out[key.removeprefix(prefix)] = float(raw)
    return out


def load_state(redis: Redis) -> RiskState:
    capital = float(redis.get(KEY_CAPITAL) or 0.0)
    if capital <= 0:
        # Bootstrap capital must be set externally; fail loud rather than
        # silently treating an unconfigured system as valid.
        raise RuntimeError(f"{KEY_CAPITAL} unset — risk-engine cannot evaluate rules")

    daily_pnl = float(redis.get(KEY_DAILY_PNL) or 0.0)
    return RiskState(
        capital_usd=capital,
        daily_pnl_usd=daily_pnl,
        daily_loss_pct=(-daily_pnl / capital * 100.0) if daily_pnl < 0 else 0.0,
        open_position_count=int(redis.get(KEY_OPEN_POSITIONS) or 0),
        exchange_exposure_pct=_scan_prefix(redis, PREFIX_EXPOSURE),
        asset_concentration_pct=_scan_prefix(redis, PREFIX_CONCENTRATION),
        leverage_multiplier=float(redis.get(KEY_LEVERAGE) or 1.0),
        kill_switch=load_kill_switch(redis),
        last_updated=datetime.fromisoformat(
            redis.get(KEY_LAST_UPDATED) or datetime.utcnow().isoformat()
        ),
    )


def load_kill_switch(redis: Redis) -> KillSwitchState:
    active = redis.get(KEY_KILL_ACTIVE) == "1"
    meta_raw = redis.get(KEY_KILL_META)
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


def set_kill_switch(redis: Redis, triggered_by: str, reason: str) -> KillSwitchState:
    state = KillSwitchState(
        active=True,
        triggered_at=datetime.utcnow(),
        triggered_by=triggered_by,
        reason=reason,
    )
    pipe = redis.pipeline()
    pipe.set(KEY_KILL_ACTIVE, "1")
    pipe.set(
        KEY_KILL_META,
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


def clear_kill_switch(redis: Redis) -> None:
    pipe = redis.pipeline()
    pipe.delete(KEY_KILL_ACTIVE)
    pipe.delete(KEY_KILL_META)
    pipe.execute()


# ── Mutable risk-state writers ───────────────────────────────────────────────
# These are the ONLY place risk:daily_pnl_usd / risk:open_positions /
# risk:exposure:* / risk:concentration:* are written (the risk-engine is the
# sole writer of risk:* keys). They are driven by trade fills via
# position_tracker.apply_trade so the drawdown guard and position-limit checks
# operate on real state instead of perpetual zeros.


def _touch_last_updated(redis: Redis) -> None:
    redis.set(KEY_LAST_UPDATED, datetime.utcnow().isoformat())


def incr_daily_pnl(redis: Redis, amount_usd: float) -> float:
    """Add realized PnL to the rolling daily total. Returns the new total."""
    new_total = float(redis.incrbyfloat(KEY_DAILY_PNL, amount_usd))
    _touch_last_updated(redis)
    return new_total


def reset_daily_pnl(redis: Redis) -> None:
    """Reset the daily PnL counter — call at the UTC day boundary."""
    redis.set(KEY_DAILY_PNL, 0.0)
    _touch_last_updated(redis)


def record_open_position(
    redis: Redis,
    opportunity_id: str,
    asset: str,
    exposure_pct_by_exchange: dict[str, float],
    concentration_pct: float,
) -> None:
    """Record an opened position and roll its contribution into the aggregates.

    Idempotent on ``opportunity_id``: a duplicate OPEN for an already-tracked
    position is ignored so a redelivered fill cannot double-count exposure.
    """
    pos_key = f"{PREFIX_POSITION}{opportunity_id}"
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
        pipe.incrbyfloat(f"{PREFIX_EXPOSURE}{exchange}", pct)
    pipe.incrbyfloat(f"{PREFIX_CONCENTRATION}{asset}", concentration_pct)
    pipe.incr(KEY_OPEN_POSITIONS)
    pipe.set(KEY_LAST_UPDATED, datetime.utcnow().isoformat())
    pipe.execute()


def release_position(redis: Redis, opportunity_id: str) -> bool:
    """Reverse a previously-recorded open position. No-op if not tracked.

    Paper trades arrive as a single CLOSED record with no prior OPEN, so this
    simply returns False for them (nothing to release). Returns True when an
    open position was found and unwound.
    """
    pos_key = f"{PREFIX_POSITION}{opportunity_id}"
    raw = redis.get(pos_key)
    if raw is None:
        return False
    record = json.loads(raw)

    pipe = redis.pipeline()
    for exchange, pct in record.get("exposure", {}).items():
        pipe.incrbyfloat(f"{PREFIX_EXPOSURE}{exchange}", -float(pct))
    pipe.incrbyfloat(f"{PREFIX_CONCENTRATION}{record['asset']}", -float(record.get("concentration", 0.0)))
    pipe.decr(KEY_OPEN_POSITIONS)
    pipe.delete(pos_key)
    pipe.set(KEY_LAST_UPDATED, datetime.utcnow().isoformat())
    pipe.execute()
    _clamp_nonnegative_aggregates(redis, record)
    return True


def _clamp_nonnegative_aggregates(redis: Redis, record: dict) -> None:
    """Guard against float drift / over-release leaving small negatives.

    Exposure and open-position counts are physically non-negative; clamp the
    keys this release touched back to >= 0 and drop ~zero exposure keys so
    /state stays clean.
    """
    keys = [f"{PREFIX_EXPOSURE}{ex}" for ex in record.get("exposure", {})]
    keys.append(f"{PREFIX_CONCENTRATION}{record['asset']}")
    for key in keys:
        raw = redis.get(key)
        if raw is None:
            continue
        val = float(raw)
        if val <= 1e-9:
            redis.delete(key)
    if int(redis.get(KEY_OPEN_POSITIONS) or 0) < 0:
        redis.set(KEY_OPEN_POSITIONS, 0)
