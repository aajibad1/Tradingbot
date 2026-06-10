"""Idempotency-Key handling for write operations (docs/06).

Doc 06 requires an idempotency key on write operations: a client retrying a
``POST /v1/onramp/orders`` (network blip, timeout) must not create two orders. The
pattern is store-and-replay — the first call's response is cached under the key,
and any retry with the same key returns the stored response instead of re-running.

Two backends share one interface so services pick by what they have:
  - :class:`InMemoryIdempotencyStore` — process-local, for tests / single-instance.
  - :class:`RedisIdempotencyStore` — shared across Cloud Run instances (the prod
    choice; Redis is already the platform's hot-state store).

This is deliberately a building block, not a decorator: where to scope the key
(per-tenant, per-route) and what counts as the cached response is the caller's
call. ``IDEMPOTENCY_KEY_HEADER`` + :func:`idempotency_key` read the header.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

IDEMPOTENCY_KEY_HEADER = "idempotency-key"

# Default TTL: long enough to cover client retries, short enough to bound storage.
DEFAULT_TTL_SECONDS = 24 * 60 * 60


def idempotency_key(request) -> str | None:
    """Read the Idempotency-Key header from a Starlette/FastAPI request (or None)."""
    return request.headers.get(IDEMPOTENCY_KEY_HEADER)


class IdempotencyStore(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def put(self, key: str, value: dict[str, Any], ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None: ...


class InMemoryIdempotencyStore:
    """Process-local store (no TTL eviction — for tests / single instance)."""

    def __init__(self) -> None:
        self._kv: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._kv.get(key)

    def put(self, key: str, value: dict[str, Any], ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._kv[key] = value


class RedisIdempotencyStore:
    """Redis-backed store shared across instances. Values are JSON; keys are
    namespaced under ``idem:`` so they never collide with other Redis state."""

    def __init__(self, redis, namespace: str = "idem") -> None:
        self._redis = redis
        self._ns = namespace

    def _k(self, key: str) -> str:
        return f"{self._ns}:{key}"

    def get(self, key: str) -> dict[str, Any] | None:
        raw = self._redis.get(self._k(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def put(self, key: str, value: dict[str, Any], ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis.set(self._k(key), json.dumps(value), ex=ttl_seconds)
