"""Secret Manager wrapper for fx-rate-service. Returns None when unavailable
(graceful degradation — the service falls back to the keyless provider)."""

from __future__ import annotations

from functools import lru_cache

from shared.utils.secret_loader import get_secret as _shared_get_secret


@lru_cache(maxsize=16)
def get_secret(secret_id: str, version: str = "latest") -> str | None:
    return _shared_get_secret(secret_id, version, required=False)
