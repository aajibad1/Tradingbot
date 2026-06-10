"""Sandbox on-ramp provider — a deterministic simulator. NO real money moves.

Sandbox-first is the documented path (docs/06 "sandbox tier", docs/12 "generate
sandbox key → test first API call"); production rails are gated on money-
transmission licensing (docs/REGULATORY_BRIEF.md). This module is the ONLY
provider wired today, and :func:`assert_sandbox_only` fails loud if anything tries
to point the service at a real provider before that gate clears.

Pricing is deterministic (fixed fee bps + a per-corridor reference rate) so tests
and demos are reproducible. The order lifecycle advances on explicit ``advance``
calls (cron-ping style, matching the repo's /tick convention) rather than wall-
clock timers, so it is fully testable: pending → processing → completed.
"""

from __future__ import annotations

import os

from shared.http import APIError, Status

# Deterministic sandbox pricing.
SANDBOX_FEE_BPS = 100.0  # 1.00% on-ramp fee
# Reference rate = units of fiat per 1 unit of dest asset (sandbox constants).
_SANDBOX_RATES: dict[str, float] = {
    "NGN": 1600.0,   # ₦/USD-stable
    "KES": 130.0,
    "GHS": 15.0,
    "USD": 1.0,
}
_DEFAULT_RATE = 1000.0


def assert_sandbox_only() -> None:
    """Refuse to run against a real provider until licensing clears.

    The service ships sandbox-only. Setting ONRAMP_PROVIDER to anything other than
    'sandbox' is a deliberate, blocked action (no real rails are implemented)."""
    provider = os.environ.get("ONRAMP_PROVIDER", "sandbox").lower()
    if provider != "sandbox":
        raise APIError(
            "provider_not_available",
            f"on-ramp provider {provider!r} is not available — sandbox only "
            "(real rails gated on licensing; see docs/REGULATORY_BRIEF.md)",
            http_status=503,
        )


def rate_for(source_currency: str) -> float:
    return _SANDBOX_RATES.get(source_currency.upper(), _DEFAULT_RATE)


def quote(source_currency: str, dest_asset: str, amount: float) -> tuple[float, float, float]:
    """Return ``(fee, rate, dest_amount)`` for a sandbox on-ramp.

    fee is in source_currency; dest_amount is in dest_asset after fee."""
    assert_sandbox_only()
    if amount <= 0:
        raise APIError("invalid_amount", "amount must be positive", http_status=422)
    rate = rate_for(source_currency)
    fee = round(amount * SANDBOX_FEE_BPS / 10_000.0, 2)
    net_source = amount - fee
    dest_amount = round(net_source / rate, 6)
    return fee, rate, dest_amount


# Sandbox order lifecycle: each advance() moves one step toward completion.
_NEXT_STATUS = {
    Status.PENDING: Status.PROCESSING,
    Status.PROCESSING: Status.COMPLETED,
}


def next_status(current: str) -> str:
    """The next sandbox status, or the same status if already terminal."""
    return _NEXT_STATUS.get(current, current)
