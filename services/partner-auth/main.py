"""partner-auth — API-key issuance, verification, rotation, revocation (docs/05, 09).

The keystone of the API plane: partners get scoped, environment-tagged API keys;
every other API-plane service authenticates inbound requests by calling
``POST /v1/partner/keys/verify`` (or, later, a shared dependency). Secrets are
hashed at rest (keystore.py) and shown exactly once.

Production-key issuance is gated: sandbox keys are free (sandbox-first, docs/04),
but ``environment=production`` requires PARTNER_PROD_KEYS_ENABLED=true — the same
fail-closed posture as the on/off-ramp live-rail guards, pending the approval +
licensing flow.

Endpoints (docs/06):
  GET  /healthz
  POST /v1/partner/keys                 issue (returns the token ONCE)
  GET  /v1/partner/keys?tenant=         list (hashes/secrets never exposed)
  POST /v1/partner/keys/{id}/rotate     new secret for the same key id
  POST /v1/partner/keys/{id}/revoke     deactivate
  POST /v1/partner/keys/verify          {token} -> {valid, tenant_id, environment, scopes}
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

import keystore
from models import APIKey, IssuedKey, KeyCreate, VerifyRequest, VerifyResult
from shared.http import APIError, install_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("partner-auth")

PRODUCER = "partner-auth"

# In-memory key store, keyed by key_id (single-instance sandbox; prod: Cloud SQL).
_keys: dict[str, APIKey] = {}

app = FastAPI(title="partner-auth", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _prod_keys_enabled() -> bool:
    return os.environ.get("PARTNER_PROD_KEYS_ENABLED", "false").lower() == "true"


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/partner/keys", response_model=IssuedKey)
def issue_key(req: KeyCreate) -> IssuedKey:
    if req.environment not in keystore.ENVIRONMENTS:
        raise APIError("invalid_environment",
                       f"environment must be one of {keystore.ENVIRONMENTS}", http_status=422)
    if req.environment == "production" and not _prod_keys_enabled():
        raise APIError(
            "production_keys_disabled",
            "production API keys are not enabled — sandbox first "
            "(approval + licensing required; see docs/REGULATORY_BRIEF.md)",
            http_status=403,
        )
    key_id = keystore.new_key_id(req.environment)
    secret = keystore.new_secret()
    _keys[key_id] = APIKey(
        key_id=key_id, tenant_id=req.tenant_id, environment=req.environment,
        scopes=req.scopes, label=req.label, active=True, created_at=_now(),
        secret_hash=keystore.hash_secret(secret),
    )
    logger.info("issued key %s tenant=%s env=%s", key_id, req.tenant_id, req.environment)
    return IssuedKey(
        key_id=key_id, token=keystore.make_token(key_id, secret),
        tenant_id=req.tenant_id, environment=req.environment, scopes=req.scopes,
    )


@app.get("/v1/partner/keys")
def list_keys(tenant: str | None = None) -> dict[str, Any]:
    out = [k.public() for k in _keys.values() if tenant is None or k.tenant_id == tenant]
    return {"keys": out, "count": len(out)}


def _get_key(key_id: str) -> APIKey:
    key = _keys.get(key_id)
    if key is None:
        raise APIError("key_not_found", f"key {key_id} not found", http_status=404)
    return key


@app.post("/v1/partner/keys/{key_id}/rotate", response_model=IssuedKey)
def rotate_key(key_id: str) -> IssuedKey:
    key = _get_key(key_id)
    secret = keystore.new_secret()
    key.secret_hash = keystore.hash_secret(secret)
    _keys[key_id] = key
    logger.info("rotated key %s", key_id)
    return IssuedKey(
        key_id=key_id, token=keystore.make_token(key_id, secret),
        tenant_id=key.tenant_id, environment=key.environment, scopes=key.scopes,
    )


@app.post("/v1/partner/keys/{key_id}/revoke")
def revoke_key(key_id: str) -> dict[str, Any]:
    key = _get_key(key_id)
    key.active = False
    _keys[key_id] = key
    return {"key_id": key_id, "active": False}


@app.post("/v1/partner/keys/verify", response_model=VerifyResult)
def verify_key(req: VerifyRequest) -> VerifyResult:
    """Verify a presented token. Fail-closed: any problem → valid=False with a
    reason, never an exception, so callers can treat this as a clean gate."""
    parsed = keystore.parse_token(req.token)
    if parsed is None:
        return VerifyResult(valid=False, reason="malformed_token")
    key_id, secret = parsed
    key = _keys.get(key_id)
    if key is None:
        return VerifyResult(valid=False, reason="unknown_key")
    if not key.active:
        return VerifyResult(valid=False, reason="revoked")
    if not keystore.secret_matches(secret, key.secret_hash):
        return VerifyResult(valid=False, reason="bad_secret")
    key.last_used_at = _now()
    _keys[key_id] = key
    return VerifyResult(
        valid=True, tenant_id=key.tenant_id,
        environment=key.environment, scopes=key.scopes,
    )
