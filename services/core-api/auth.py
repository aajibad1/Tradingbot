"""Identity verification — a pluggable IdP adapter with a local-dev bypass.

core-api is the system-of-record for tenancy/RBAC; the *identity provider* that
issues the session token sits behind this one seam. Per docs the prod IdP is
**Clerk** (verify its JWT via JWKS), but the rest of the service never depends on
which provider it is — it only consumes an ``Identity``. Swapping Clerk⇄Firebase
is implementing one ``_verify_*`` function here; nothing else changes.

AUTH_PROVIDER selects the adapter:
  * unset / "local"  — dev/test: a bearer of the form ``local:<uid>[:<email>]`` is
    accepted verbatim. NOT a security control; mirrors the GCP_PROJECT_ID-unset
    local mode used elsewhere in the repo.
  * "clerk"          — verify a Clerk session JWT (JWKS). [adapter: TODO]
  * "firebase"       — verify a Firebase ID token. [adapter: TODO]
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class Identity:
    uid: str
    email: str | None = None


def _parse_local(token: str) -> Identity:
    # "local:uid" or "local:uid:email"
    parts = token.split(":", 2)
    if len(parts) < 2 or not parts[1]:
        raise HTTPException(status_code=401, detail="local token must be 'local:<uid>[:<email>]'")
    return Identity(uid=parts[1], email=parts[2] if len(parts) == 3 else None)


def _verify_clerk(token: str) -> Identity:  # pragma: no cover - adapter stub
    """Verify a Clerk session JWT via JWKS and map sub→uid, email claim→email.
    Wire to Clerk's JWKS endpoint + signature/exp checks when Clerk is provisioned."""
    raise HTTPException(status_code=501, detail="clerk auth adapter not yet wired")


def _verify_firebase(token: str) -> Identity:  # pragma: no cover - adapter stub
    raise HTTPException(status_code=501, detail="firebase auth adapter not yet wired")


def verify_bearer(authorization: str | None) -> Identity:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    token = authorization[len("bearer "):].strip()

    provider = os.environ.get("AUTH_PROVIDER", "local").lower()
    if provider == "local":
        if not token.startswith("local:"):
            raise HTTPException(
                status_code=401,
                detail="local mode: expected a 'local:<uid>[:<email>]' bearer (AUTH_PROVIDER=local)",
            )
        return _parse_local(token)
    if provider == "clerk":
        return _verify_clerk(token)
    if provider == "firebase":
        return _verify_firebase(token)
    raise HTTPException(status_code=500, detail=f"unknown AUTH_PROVIDER {provider!r}")


def current_identity(authorization: str | None = Header(default=None)) -> Identity:
    """FastAPI dependency — the authenticated caller, or 401."""
    return verify_bearer(authorization)
