"""Scrub secrets out of strings before they are logged or surfaced.

Some upstream errors embed credentials: httpx's ``HTTPStatusError`` includes
the full request URL, and CryptoPanic passes its ``auth_token`` as a query
parameter — so a raw ``str(exc)`` can leak the token into Cloud Logging or a
``/healthz`` payload. Run any externally-derived error/URL string through
:func:`redact_secrets` before logging it.
"""

from __future__ import annotations

import re

# Query-param style: auth_token=..., api_key=..., apikey=..., token=...,
# access_token=..., secret=..., password=..., key=... (value ends at & or quote/space).
_QUERY_SECRET_RE = re.compile(
    r"(?i)\b(auth_token|access[-_]?token|api[-_]?key|apikey|token|secret|password|key)"
    r"=([^&\s'\"]+)"
)
# Authorization: Bearer <token>
_BEARER_RE = re.compile(r"(?i)(bearer\s+)\S+")


def redact_secrets(text: str) -> str:
    """Return ``text`` with common credential patterns masked as ``***``."""
    if not text:
        return text
    redacted = _QUERY_SECRET_RE.sub(r"\1=***", text)
    redacted = _BEARER_RE.sub(r"\1***", redacted)
    return redacted
