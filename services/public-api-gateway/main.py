"""public-api-gateway — authenticated, metered front door to the API plane (docs/05).

Every partner request flows through here:
  1. authenticate the Bearer API key against partner-auth (/v1/partner/keys/verify),
  2. check the key holds the scope for the target service,
  3. meter the call in api-metering (fail-open — a metering outage must not drop
     traffic; we bill on a best-effort counter),
  4. proxy to the upstream service and return its response verbatim.

Correlation ids (shared.http) propagate to the upstream so a request is traceable
end to end. SANDBOX: upstreams are the local sandbox services (env-configured).

Config (env): PARTNER_AUTH_URL, API_METERING_URL, and per-service upstream URLs
(ONRAMP_URL, OFFRAMP_URL, WALLET_URL, ROUTING_URL, SETTLEMENT_URL) — see gateway.py.

Routes:
  GET  /healthz
  ANY  /api/{service}/{path}      authenticated + metered proxy
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI, Request, Response

import gateway
from shared.http import (
    APIError,
    get_correlation_id,
    get_request_id,
    install_contract,
)
from shared.http.correlation import CORRELATION_ID_HEADER, REQUEST_ID_HEADER

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("public-api-gateway")

PRODUCER = "public-api-gateway"
UPSTREAM_TIMEOUT_S = 15.0

app = FastAPI(title="public-api-gateway", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def _verify(token: str) -> dict:
    """Verify the key with partner-auth. Raises APIError(401) if invalid."""
    url = os.environ.get("PARTNER_AUTH_URL")
    if not url:
        raise APIError("auth_unavailable", "PARTNER_AUTH_URL not configured", http_status=503)
    try:
        r = httpx.post(f"{url.rstrip('/')}/v1/partner/keys/verify",
                       json={"token": token}, timeout=5.0)
        r.raise_for_status()
        result = r.json()
    except APIError:
        raise
    except Exception:
        # Fail-CLOSED on auth: if we cannot verify, we do not let the request in.
        raise APIError("auth_unavailable", "could not verify credentials", http_status=503)
    if not result.get("valid"):
        raise APIError("unauthorized", f"invalid API key ({result.get('reason')})", http_status=401)
    return result


def _meter(tenant: str, route: str) -> None:
    """Record one usage unit. FAIL-OPEN: a metering outage must not drop traffic."""
    url = os.environ.get("API_METERING_URL")
    if not url:
        return
    try:
        httpx.post(f"{url.rstrip('/')}/v1/metering/record",
                   json={"tenant_id": tenant, "route": route, "count": 1}, timeout=3.0)
    except Exception:
        logger.warning("metering record failed for tenant=%s route=%s (fail-open)", tenant, route)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route("/api/{service}/{path:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(service: str, path: str, request: Request) -> Response:
    if not gateway.is_known(service):
        raise APIError("unknown_service", f"no such service {service!r}", http_status=404)

    token = _bearer(request)
    if not token:
        raise APIError("unauthorized", "missing Bearer API key", http_status=401)

    identity = _verify(token)
    scope = gateway.required_scope(service)
    if not gateway.has_scope(identity.get("scopes", []), scope):
        raise APIError("forbidden", f"key lacks scope {scope!r} for {service}", http_status=403)

    base = gateway.upstream_base(service)
    if base is None:
        raise APIError("service_unavailable", f"{service} upstream not configured", http_status=502)

    tenant = identity.get("tenant_id", "unknown")
    route = f"{request.method} /api/{service}"
    _meter(tenant, route)

    # Proxy verbatim, propagating tenant + correlation so the upstream can trace it.
    body = await request.body()
    fwd_headers = {
        "content-type": request.headers.get("content-type", "application/json"),
        "x-tenant-id": tenant,
        REQUEST_ID_HEADER: get_request_id(request) or "",
        CORRELATION_ID_HEADER: get_correlation_id(request) or "",
    }
    url = f"{base}/{path}"
    try:
        upstream = httpx.request(request.method, url, params=dict(request.query_params),
                                 content=body, headers=fwd_headers, timeout=UPSTREAM_TIMEOUT_S)
    except Exception:
        raise APIError("upstream_error", f"{service} upstream unreachable", http_status=502)

    media = upstream.headers.get("content-type", "application/json")
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=media)
