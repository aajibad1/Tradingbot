"""Sandbox off-ramp provider — a deterministic simulator. NO real money moves.

Mirror of the on-ramp sandbox (services/onramp-orchestrator) for the payout
direction (stablecoin → fiat). Sandbox-first is the documented path; production
payout rails are gated on money-transmission licensing (docs/REGULATORY_BRIEF.md),
and :func:`assert_sandbox_only` fails loud if anything points the service at a
real provider before that gate clears.

Deterministic pricing (fixed fee bps + per-currency reference rate); the order
lifecycle advances on explicit ``advance`` calls (cron-ping), not wall-clock
timers, so it is fully testable: pending → processing → completed.
"""

from __future__ import annotations

import os

from shared.http import APIError, Status

# Deterministic sandbox pricing. Off-ramp fee is slightly higher than on-ramp
# (payout rails cost more) — purely a sandbox constant.
SANDBOX_FEE_BPS = 150.0  # 1.50% off-ramp fee
# Reference rate = units of fiat per 1 unit of source (stablecoin) asset.
_SANDBOX_RATES: dict[str, float] = {
    "NGN": 1600.0,
    "KES": 130.0,
    "GHS": 15.0,
    "USD": 1.0,
}
_DEFAULT_RATE = 1000.0


def assert_sandbox_only() -> None:
    """Refuse to run against a real payout provider until licensing clears."""
    provider = os.environ.get("OFFRAMP_PROVIDER", "sandbox").lower()
    if provider != "sandbox":
        raise APIError(
            "provider_not_available",
            f"off-ramp provider {provider!r} is not available — sandbox only "
            "(real rails gated on licensing; see docs/REGULATORY_BRIEF.md)",
            http_status=503,
        )


def rate_for(dest_currency: str) -> float:
    return _SANDBOX_RATES.get(dest_currency.upper(), _DEFAULT_RATE)


def quote(source_asset: str, dest_currency: str, amount: float) -> tuple[float, float, float]:
    """Return ``(fee, rate, dest_amount)`` for a sandbox off-ramp.

    fee is in source_asset (stablecoin); dest_amount is fiat after fee."""
    assert_sandbox_only()
    if amount <= 0:
        raise APIError("invalid_amount", "amount must be positive", http_status=422)
    rate = rate_for(dest_currency)
    fee = round(amount * SANDBOX_FEE_BPS / 10_000.0, 6)
    net_source = amount - fee
    dest_amount = round(net_source * rate, 2)
    return fee, rate, dest_amount


# Sandbox order lifecycle: each advance() moves one step toward completion.
_NEXT_STATUS = {
    Status.PENDING: Status.PROCESSING,
    Status.PROCESSING: Status.COMPLETED,
}


def next_status(current: str) -> str:
    return _NEXT_STATUS.get(current, current)
