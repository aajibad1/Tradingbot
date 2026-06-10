"""admin-console — ops/compliance control surface (docs/04 Control Console, docs/14).

A FastAPI-served single-page ops console (no npm, no CORS — the UI calls only this
service's /admin/* endpoints, which proxy server-side). Aggregates oversight across
the platform: live service health (status-service), per-tenant partner keys
(partner-auth), usage (api-metering), invoices (tenant-billing), and webhook
deliveries (webhook-service).

Read-only oversight — it never mutates partner state. Config (env, local defaults):
  STATUS_URL, PARTNER_AUTH_URL, API_METERING_URL, WEBHOOK_URL, BILLING_URL

Routes:
  GET /                    the console UI
  GET /healthz
  GET /admin/health        platform status (best-effort; never 502s the console)
  GET /admin/keys?tenant=
  GET /admin/usage?tenant=
  GET /admin/invoices?tenant=
  GET /admin/deliveries
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from shared.http import APIError, install_contract
from ui import INDEX_HTML

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("admin-console")

PRODUCER = "admin-console"

_DEFAULTS = {
    "STATUS_URL": "http://127.0.0.1:8087",
    "PARTNER_AUTH_URL": "http://127.0.0.1:8211",
    "API_METERING_URL": "http://127.0.0.1:8212",
    "WEBHOOK_URL": "http://127.0.0.1:8216",
    "BILLING_URL": "http://127.0.0.1:8217",
}

app = FastAPI(title="admin-console", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _url(key: str) -> str:
    return os.environ.get(key, _DEFAULTS[key]).rstrip("/")


def _get(url: str, params: dict | None = None) -> Any:
    """Proxy a GET; raises APIError(502) if the upstream is unreachable."""
    try:
        r = httpx.get(url, params=params, timeout=10.0)
    except Exception:
        raise APIError("upstream_unreachable", f"could not reach {url}", http_status=502)
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/admin/health")
def health() -> dict[str, Any]:
    """Platform status — BEST-EFFORT: if status-service is down the console must
    still render, so we report reachable=false instead of failing."""
    try:
        r = httpx.get(f"{_url('STATUS_URL')}/status", timeout=5.0)
        return {"reachable": True, "http_status": r.status_code, "report": r.json()}
    except Exception:
        return {"reachable": False, "report": None}


@app.get("/admin/keys")
def keys(tenant: str | None = None) -> Any:
    return _get(f"{_url('PARTNER_AUTH_URL')}/v1/partner/keys",
                params={"tenant": tenant} if tenant else None)


@app.get("/admin/usage")
def usage(tenant: str) -> Any:
    return _get(f"{_url('API_METERING_URL')}/v1/metering/usage", params={"tenant": tenant})


@app.get("/admin/invoices")
def invoices(tenant: str | None = None) -> Any:
    return _get(f"{_url('BILLING_URL')}/v1/billing/invoices",
                params={"tenant": tenant} if tenant else None)


@app.get("/admin/deliveries")
def deliveries() -> Any:
    return _get(f"{_url('WEBHOOK_URL')}/v1/deliveries")
