"""Secret Manager wrapper. All exchange API keys are fetched at runtime,
never read from env files or baked into images.

Thin wrapper over ``shared.utils.secret_loader`` with the market-data policy:
secrets are *required* — a missing/unconfigured secret raises (fail loud), so
exchange credentials can never silently come back empty.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from shared.tenant import DEFAULT_TENANT
from shared.utils.secret_loader import get_exchange_credentials as _shared_get_exchange_credentials
from shared.utils.secret_loader import get_secret as _shared_get_secret

logger = logging.getLogger(__name__)


@lru_cache(maxsize=128)
def get_secret(secret_id: str, version: str = "latest") -> str:
    """Fetch a required secret — raises RuntimeError if unavailable."""
    value = _shared_get_secret(secret_id, version, required=True)
    # required=True never returns None, but assert the contract for type-checkers.
    assert value is not None
    return value


def get_exchange_credentials(exchange: str, tenant_id: str = DEFAULT_TENANT) -> dict[str, str]:
    """Per-tenant exchange credentials (delegates to the shared resolver).

    ``default`` tenant ⇒ shared platform keys (``arb-{ex}-key``); a user tenant
    ⇒ that user's own keys (``user-{uid}-{ex}-key``). Withdrawal permissions MUST
    be disabled when these keys are issued.
    """
    return _shared_get_exchange_credentials(exchange, tenant_id)
