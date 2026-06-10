"""API-key token logic (docs/09 API key hashing + rotation). Pure + testable.

A key has a public id (``pk_<env>_<hex>``) and a one-time secret; the partner
receives ``<key_id>.<secret>`` once at creation. We store ONLY a SHA-256 hash of
the secret — never the secret itself — so a store compromise cannot recover live
credentials. Verification re-hashes the presented secret and compares in constant
time. Rotation issues a new secret (same key_id); revocation deactivates it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

ENVIRONMENTS = ("sandbox", "production")


def new_key_id(environment: str) -> str:
    return f"pk_{environment}_{secrets.token_hex(8)}"


def new_secret() -> str:
    return secrets.token_hex(24)


def make_token(key_id: str, secret: str) -> str:
    """The full credential handed to the partner once: ``<key_id>.<secret>``."""
    return f"{key_id}.{secret}"


def parse_token(token: str) -> tuple[str, str] | None:
    """Split a presented token into ``(key_id, secret)``, or None if malformed."""
    if not token or token.count(".") != 1:
        return None
    key_id, secret = token.split(".", 1)
    if not key_id.startswith("pk_") or not secret:
        return None
    return key_id, secret


def env_of(key_id: str) -> str | None:
    """The environment encoded in a key id (``pk_<env>_<hex>``)."""
    parts = key_id.split("_")
    return parts[1] if len(parts) >= 3 and parts[0] == "pk" else None


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def secret_matches(secret: str, secret_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(secret), secret_hash)
