"""Secret Manager wrapper for funding rate API keys.

Falls back to LOCAL_SECRET_<UPPER_SNAKE_ID> env vars when GCP_PROJECT_ID is unset
(local dev pattern).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def get_secret(secret_id: str, version: str = "latest") -> str | None:
    """Fetch a secret from GCP Secret Manager.

    For local dev, falls back to env var `LOCAL_SECRET_<secret_id_upper>`.
    Returns None if both GCP and local env are unavailable (graceful degradation).
    """
    local_env_key = f"LOCAL_SECRET_{secret_id.upper().replace('-', '_')}"
    if os.environ.get(local_env_key):
        return os.environ[local_env_key]

    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        logger.warning(
            "secret %r requested but GCP_PROJECT_ID and %s both unset — returning None",
            secret_id,
            local_env_key,
        )
        return None

    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/{version}"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8")
    except Exception as e:
        logger.warning("failed to fetch secret %r: %s", secret_id, e)
        return None
