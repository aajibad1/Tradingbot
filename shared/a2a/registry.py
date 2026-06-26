"""A2A discovery — resolve a peer agent's base URL by name.

The roster of A2A-speaking agents is small and known at build time, so discovery
is a static name→URL map with per-agent env overrides (``A2A_<NAME>_URL``, where
NAME is the upper-snake agent name). This mirrors how the rest of the platform
resolves peers (no service mesh locally); a service-registry backend can replace
``base_url`` later without changing call sites.

    from shared.a2a import A2AClient, base_url
    verdict = A2AClient(base_url("debate-service")).send_text("funding carry is real")
"""
from __future__ import annotations

import os

from .client import A2AClient

# Default local ports for the A2A-speaking agent services (used by local_stack).
_DEFAULT_PORTS: dict[str, int] = {
    "debate-service": 8340,
    "approval-gate-service": 8341,
    "agent-registry": 8342,
    "agent-evals": 8343,
    "ai-ops-agent": 8344,
}

A2A_AGENTS = frozenset(_DEFAULT_PORTS)


def _env_key(name: str) -> str:
    return "A2A_" + name.upper().replace("-", "_") + "_URL"


def base_url(name: str) -> str:
    """Resolve an agent's A2A base URL: ``A2A_<NAME>_URL`` env override, else the
    local default (``http://localhost:<port>``). Raises for an unknown agent so a
    typo fails loud rather than silently pointing nowhere."""
    override = os.environ.get(_env_key(name))
    if override:
        return override.rstrip("/")
    if name not in _DEFAULT_PORTS:
        raise KeyError(f"unknown A2A agent: {name!r}")
    return f"http://localhost:{_DEFAULT_PORTS[name]}"


def client_for(name: str, **kwargs) -> A2AClient:
    """A2AClient pointed at the named agent (convenience over ``base_url``)."""
    return A2AClient(base_url(name), **kwargs)
