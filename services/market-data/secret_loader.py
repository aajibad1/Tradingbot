"""Secret Manager wrapper. All exchange API keys are fetched at runtime,
never read from env files or baked into images.

Thin wrapper over ``shared.utils.secret_loader`` with the market-data policy:
secrets are *required* — a missing/unconfigured secret raises (fail loud), so
exchange credentials can never silently come back empty.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from shared.utils.secret_loader import get_secret as _shared_get_secret

logger = logging.getLogger(__name__)


@lru_cache(maxsize=128)
def get_secret(secret_id: str, version: str = "latest") -> str:
    """Fetch a required secret — raises RuntimeError if unavailable."""
    value = _shared_get_secret(secret_id, version, required=True)
    # required=True never returns None, but assert the contract for type-checkers.
    assert value is not None
    return value


def get_exchange_credentials(exchange: str) -> dict[str, str]:
    """Returns {'apiKey': ..., 'secret': ..., 'password': ...} where applicable.

    Secret naming convention: `arb-<exchange>-{key,secret,password}`.
    Withdrawal permissions MUST be disabled at the exchange when these keys
    are issued — enforced at exchange level AND in Secret Manager IAM.
    """
    exchange_l = exchange.lower().replace(".", "")
    creds = {
        "apiKey": get_secret(f"arb-{exchange_l}-key"),
        "secret": get_secret(f"arb-{exchange_l}-secret"),
    }
    # Coinbase Advanced + Kraken use a passphrase / API password
    if exchange_l in {"coinbase", "kraken"}:
        try:
            creds["password"] = get_secret(f"arb-{exchange_l}-password")
        except RuntimeError:
            logger.info("no password secret for %s — proceeding without", exchange_l)
    return creds
