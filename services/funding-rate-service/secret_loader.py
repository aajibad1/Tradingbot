"""Secret Manager wrapper for funding rate API keys.

Thin wrapper over ``shared.utils.secret_loader`` with the graceful-degradation
policy: returns ``None`` (rather than raising) when a secret is unavailable, so
a missing optional API key doesn't take the service down.
"""

from __future__ import annotations

from functools import lru_cache

from shared.utils.secret_loader import get_secret as _shared_get_secret


@lru_cache(maxsize=32)
def get_secret(secret_id: str, version: str = "latest") -> str | None:
    """Fetch a secret, or None if unavailable (graceful degradation)."""
    return _shared_get_secret(secret_id, version, required=False)
