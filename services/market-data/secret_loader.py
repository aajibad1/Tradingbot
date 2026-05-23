"""Secret Manager wrapper. All exchange API keys are fetched at runtime,
never read from env files or baked into images.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=128)
def get_secret(secret_id: str, version: str = "latest") -> str:
    """Fetch a secret from GCP Secret Manager.

    For local dev, falls back to env var `LOCAL_SECRET_<secret_id_upper>` so
    you can develop without a GCP project.
    """
    local_env_key = f"LOCAL_SECRET_{secret_id.upper().replace('-', '_')}"
    if os.environ.get(local_env_key):
        return os.environ[local_env_key]

    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        raise RuntimeError(
            f"secret {secret_id!r} requested but GCP_PROJECT_ID and {local_env_key} both unset"
        )

    from google.cloud import secretmanager  # local import — pulls heavy gRPC deps

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8")


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
