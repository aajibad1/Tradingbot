"""Clerk webhook sync — mirror Clerk's user lifecycle into Postgres.

Clerk is the identity source; this keeps a local copy of users so product logic
(tenancy, RBAC, onboarding, billing) joins against Postgres instead of calling
Clerk on every request. Webhooks are Svix-signed; verification is implemented
directly (HMAC-SHA256 over ``{svix_id}.{svix_timestamp}.{payload}``) so the slice
is testable offline with a known secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

# Events we mirror (subset of Clerk's catalogue).
USER_UPSERT = {"user.created", "user.updated"}
USER_DELETE = {"user.deleted"}


def verify_signature(payload: bytes, headers, secret: str | None) -> bool:
    """Verify a Svix (Clerk) webhook signature.

    ``headers`` is any mapping with svix-id / svix-timestamp / svix-signature.
    The signing secret is ``whsec_<base64>``; the signature header is a
    space-separated list of ``v1,<base64sig>`` entries."""
    if not secret:
        return False
    svix_id = headers.get("svix-id")
    svix_ts = headers.get("svix-timestamp")
    svix_sig = headers.get("svix-signature")
    if not (svix_id and svix_ts and svix_sig):
        return False
    key = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        secret_bytes = base64.b64decode(key)
    except Exception:
        return False
    signed = f"{svix_id}.{svix_ts}.".encode() + payload
    expected = base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()
    for part in svix_sig.split():
        _, _, sig = part.partition(",")
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False


def primary_email(data: dict) -> str | None:
    """Pull the primary email from a Clerk user payload."""
    addresses = data.get("email_addresses") or []
    primary_id = data.get("primary_email_address_id")
    for a in addresses:
        if a.get("id") == primary_id:
            return a.get("email_address")
    return addresses[0].get("email_address") if addresses else None
