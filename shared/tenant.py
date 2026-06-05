"""Tenant identity + namespacing — the single source of truth for per-user
isolation of Redis keys and Secret Manager ids.

See docs/MULTI_TENANCY.md. The ``default`` tenant maps to the legacy
(un-namespaced) keys so the existing single-tenant deployment is unaffected;
any other tenant gets a ``risk:t:{tenant}:`` namespace.
"""

from __future__ import annotations

import re

# The single-tenant deployment's state lives under the legacy keys; the
# ``default`` tenant preserves them so nothing has to migrate.
DEFAULT_TENANT = "default"
# Owns global controls (the platform-wide kill switch that halts every tenant).
PLATFORM_TENANT = "platform"

# Platform-wide kill switch — independent of any tenant's own kill switch.
PLATFORM_KILL_SWITCH_KEY = "platform:kill_switch:active"

_VALID_TENANT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_tenant(tenant_id: str) -> str:
    """Return ``tenant_id`` if it is a safe key/secret fragment, else raise.

    Tenants flow into Redis keys and Secret Manager ids, so they must be
    restricted to ``[A-Za-z0-9_-]`` to avoid namespace escapes.
    """
    if not _VALID_TENANT.match(tenant_id):
        raise ValueError(f"invalid tenant_id {tenant_id!r} (must match [A-Za-z0-9_-]{{1,64}})")
    return tenant_id


def risk_key(suffix: str, tenant_id: str = DEFAULT_TENANT) -> str:
    """Build a ``risk:*`` key, namespaced per tenant.

    ``default`` ⇒ the legacy ``risk:{suffix}`` key (unchanged on disk).
    Any other tenant ⇒ ``risk:t:{tenant}:{suffix}``.
    """
    if tenant_id == DEFAULT_TENANT:
        return f"risk:{suffix}"
    return f"risk:t:{validate_tenant(tenant_id)}:{suffix}"


def risk_scan_pattern(prefix: str, tenant_id: str = DEFAULT_TENANT) -> str:
    """Scan pattern for a family of risk keys (e.g. ``exposure:``) for a tenant."""
    return risk_key(f"{prefix}*", tenant_id)


def exchange_secret_id(exchange: str, kind: str, tenant_id: str = DEFAULT_TENANT) -> str:
    """Secret Manager id for an exchange credential.

    ``default`` ⇒ the shared platform key ``arb-{exchange}-{kind}``.
    Per-tenant ⇒ ``user-{tenant}-{exchange}-{kind}`` (the flat form of the
    logical path ``/users/{uid}/exchanges/{exchange}/{kind}``).
    ``kind`` is e.g. "key", "secret", "password".
    """
    ex = exchange.lower().replace(".", "")
    if tenant_id == DEFAULT_TENANT:
        return f"arb-{ex}-{kind}"
    return f"user-{validate_tenant(tenant_id)}-{ex}-{kind}"
