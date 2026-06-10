"""Webhook payload signing (docs/06 webhook signing, docs/09 verification).

Each endpoint gets a signing secret at creation. Every delivery is signed with
HMAC-SHA256 over the exact request body and sent in the ``X-Webhook-Signature``
header as ``sha256=<hex>``, plus an ``X-Webhook-Timestamp`` to bound replay. A
partner verifies by recomputing the HMAC over the raw body with their secret —
:func:`verify` is the reference implementation we hand them.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Webhook-Signature"
TIMESTAMP_HEADER = "X-Webhook-Timestamp"


def new_secret() -> str:
    """A fresh signing secret (``whsec_<hex>``)."""
    import uuid

    return f"whsec_{uuid.uuid4().hex}{uuid.uuid4().hex}"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Return ``sha256=<hex>`` over ``<timestamp>.<body>`` keyed by the secret.

    The timestamp is bound into the signed material so a captured payload can't be
    replayed under a different time."""
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def verify(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Constant-time verification of a received signature."""
    expected = sign(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)
