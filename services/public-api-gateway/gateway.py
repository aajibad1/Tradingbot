"""Gateway routing + scope config (docs/05 public-api-gateway). Pure + testable.

Maps a public path prefix (``/api/<service>/...``) to an upstream base URL (from
env) and the scope an API key must hold to use it. The gateway authenticates every
request against partner-auth, checks scope, meters via api-metering, then proxies.
"""

from __future__ import annotations

import os

# service -> (upstream base-URL env var, required scope)
SERVICES: dict[str, tuple[str, str]] = {
    "onramp": ("ONRAMP_URL", "onramp"),
    "offramp": ("OFFRAMP_URL", "offramp"),
    "wallets": ("WALLET_URL", "wallets"),
    "balances": ("WALLET_URL", "wallets"),
    "routes": ("ROUTING_URL", "routing"),
    "settlements": ("SETTLEMENT_URL", "settlement"),
}


def is_known(service: str) -> bool:
    return service in SERVICES


def required_scope(service: str) -> str:
    return SERVICES[service][1]


def upstream_base(service: str) -> str | None:
    env_var = SERVICES[service][0]
    base = os.environ.get(env_var)
    return base.rstrip("/") if base else None


def has_scope(scopes: list[str], required: str) -> bool:
    """A key grants a service if it holds the exact scope, the ``<scope>:*`` form,
    or the global ``*``."""
    return required in scopes or f"{required}:*" in scopes or "*" in scopes
