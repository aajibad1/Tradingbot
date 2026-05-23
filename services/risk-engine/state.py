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
