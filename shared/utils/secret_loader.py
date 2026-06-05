"""Shared Secret Manager wrapper — single source of truth for secret fetching.

Previously each service shipped its own near-identical ``secret_loader.py`` with
*diverging* semantics: market-data raised on a missing/unconfigured secret,
while funding-rate-service and sentiment-service returned ``None``. That split
contract was a latent bug source. This module unifies them behind one explicit
policy flag:

  * ``required=True``  → raise ``RuntimeError`` when neither a local env override
    nor ``GCP_PROJECT_ID`` is available, and let any GCP fetch error propagate.
    (market-data / exchange-credential semantics — fail loud.)
  * ``required=False`` → return ``None`` when unconfigured, and swallow GCP fetch
    errors as ``None``. (funding-rate / sentiment semantics — degrade gracefully.)

Local-dev fallback (both modes): ``LOCAL_SECRET_<UPPER_SNAKE_ID>`` env var, so the
system runs without a GCP project. The per-service ``secret_loader.py`` files are
kept as thin typed wrappers so existing call sites and signatures are unchanged.

This core is intentionally **uncached** — each per-service wrapper applies its own
``lru_cache`` (and therefore its own ``cache_clear``), preserving the prior
per-process memoisation and the test hooks that clear it.
"""

from __future__ import annotations

import logging
import os

from shared.tenant import DEFAULT_TENANT, exchange_secret_id

logger = logging.getLogger(__name__)


def _local_env_key(secret_id: str) -> str:
    return f"LOCAL_SECRET_{secret_id.upper().replace('-', '_')}"


def _fetch_from_gcp(project_id: str, secret_id: str, version: str) -> str:
    from google.cloud import secretmanager  # local import — pulls heavy gRPC deps

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8")


def get_secret(secret_id: str, version: str = "latest", *, required: bool = False) -> str | None:
    """Fetch a secret. See module docstring for the ``required`` policy."""
    local_key = _local_env_key(secret_id)
    if os.environ.get(local_key):
        return os.environ[local_key]

    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        if required:
            raise RuntimeError(
                f"secret {secret_id!r} requested but GCP_PROJECT_ID and {local_key} both unset"
            )
        logger.warning(
            "secret %r requested but GCP_PROJECT_ID and %s both unset — returning None",
            secret_id,
            local_key,
        )
        return None

    if required:
        # Fail loud: let a transient GCP error propagate (and not be cached).
        return _fetch_from_gcp(project_id, secret_id, version)

    try:
        return _fetch_from_gcp(project_id, secret_id, version)
    except Exception as e:  # noqa: BLE001 — degrade gracefully on a fetch error
        logger.warning("failed to fetch secret %r: %s", secret_id, e)
        return None


def get_exchange_credentials(exchange: str, tenant_id: str = DEFAULT_TENANT) -> dict[str, str]:
    """Resolve an exchange's API credentials for a tenant — fail loud if missing.

    - ``default`` tenant → the shared platform secrets (``arb-{ex}-key`` …),
      i.e. today's single-tenant behaviour.
    - a user tenant → THAT user's own secrets (``user-{uid}-{ex}-key`` …) with
      **no cross-tenant fallback** — a user trades only with their own keys
      (CS-04 / SEC-10 per-user secret isolation).

    Withdrawal permissions MUST be disabled at the exchange when these keys are
    issued (enforced at the exchange + Secret Manager IAM). ``key``/``secret``
    are required; the Coinbase/Kraken passphrase is optional.
    """
    ex = exchange.lower().replace(".", "")
    creds: dict[str, str] = {
        "apiKey": get_secret(exchange_secret_id(exchange, "key", tenant_id), required=True),  # type: ignore[dict-item]
        "secret": get_secret(exchange_secret_id(exchange, "secret", tenant_id), required=True),  # type: ignore[dict-item]
    }
    if ex in {"coinbase", "kraken"}:
        try:
            creds["password"] = get_secret(exchange_secret_id(exchange, "password", tenant_id), required=True)  # type: ignore[assignment]
        except RuntimeError:
            logger.info("no passphrase secret for %s/%s — proceeding without", tenant_id, ex)
    return creds
