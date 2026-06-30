"""developer-portal — self-serve key management + sandbox API playground (docs/12).

A FastAPI app that serves a single-page UI (ui.py) and proxies its actions
server-side to partner-auth and the public-api-gateway. The browser only talks to
this service's own origin, so there is no CORS surface and no Node/npm build — open
it locally and it works against the local sandbox services.

Config (env, with local defaults):
  PARTNER_AUTH_URL   (default http://127.0.0.1:8211)
  GATEWAY_URL        (default http://127.0.0.1:8218)

Routes:
  GET  /                       the portal UI
  GET  /healthz
  POST /portal/keys            issue (proxies partner-auth)
  GET  /portal/keys?tenant=    list
  POST /portal/keys/{id}/rotate
  POST /portal/keys/{id}/revoke
  POST /portal/proxy           run an API call through the gateway with a token
  GET  /portal/services        API catalog (from docs/api-catalog/*.openapi.json)
  GET  /portal/agents          live A2A agent catalog (skills + reachability)
"""

from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from shared.a2a import client_for, roster_catalog
from shared.http import APIError, install_contract
from ui import INDEX_HTML

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("developer-portal")

PRODUCER = "developer-portal"


def _catalog_dir() -> str:
    """Resolve docs/api-catalog across local (repo root) + container (/app) layouts."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("CATALOG_DIR"),
        os.path.join(os.path.dirname(os.path.dirname(here)), "docs", "api-catalog"),  # repo root
        os.path.join(here, "docs", "api-catalog"),  # container: /app/docs/api-catalog
        os.path.join(os.getcwd(), "docs", "api-catalog"),
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return candidates[1]  # default (may not exist → empty catalog, handled gracefully)

app = FastAPI(title="developer-portal", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _partner_auth() -> str:
    return os.environ.get("PARTNER_AUTH_URL", "http://127.0.0.1:8211").rstrip("/")


def _gateway() -> str:
    return os.environ.get("GATEWAY_URL", "http://127.0.0.1:8218").rstrip("/")


def _proxy(method: str, url: str, *, json_body: Any = None, params: dict | None = None,
           headers: dict | None = None) -> tuple[int, Any]:
    """Server-side call to an upstream; returns (status, parsed-or-text). Raises
    APIError(502) only when the upstream is unreachable."""
    try:
        r = httpx.request(method, url, json=json_body, params=params, headers=headers, timeout=15.0)
    except Exception:
        raise APIError("upstream_unreachable", f"could not reach {url}", http_status=502)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


class KeyIssue(BaseModel):
    tenant_id: str
    scopes: list[str] = Field(default_factory=list)


class ProxyCall(BaseModel):
    token: str
    method: str = "POST"
    service: str
    path: str
    body: dict | None = None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": "sandbox"}


@app.post("/portal/keys")
def issue_key(req: KeyIssue) -> Any:
    _, data = _proxy("POST", f"{_partner_auth()}/v1/partner/keys",
                     json_body={"tenant_id": req.tenant_id, "scopes": req.scopes})
    return data


@app.get("/portal/keys")
def list_keys(tenant: str | None = None) -> Any:
    _, data = _proxy("GET", f"{_partner_auth()}/v1/partner/keys",
                     params={"tenant": tenant} if tenant else None)
    return data


@app.post("/portal/keys/{key_id}/rotate")
def rotate_key(key_id: str) -> Any:
    _, data = _proxy("POST", f"{_partner_auth()}/v1/partner/keys/{key_id}/rotate")
    return data


@app.post("/portal/keys/{key_id}/revoke")
def revoke_key(key_id: str) -> Any:
    _, data = _proxy("POST", f"{_partner_auth()}/v1/partner/keys/{key_id}/revoke")
    return data


@app.post("/portal/proxy")
def try_api(req: ProxyCall) -> dict[str, Any]:
    """Run one API call through the gateway with the supplied Bearer token, so the
    playground exercises the real auth+meter+proxy path."""
    url = f"{_gateway()}/api/{req.service}/{req.path.lstrip('/')}"
    status, data = _proxy(req.method.upper(), url, json_body=req.body,
                          headers={"authorization": f"Bearer {req.token}"})
    return {"status": status, "response": data}


@app.get("/portal/agents")
def agents() -> dict[str, Any]:
    """Live A2A agent catalog — which governance/intelligence agents are answering
    and the skills they advertise (from each agent's published card). Best-effort
    and time-bounded (2s/peer): a down agent is listed under ``unreachable`` rather
    than failing the page. ``status`` is ``ok`` when the whole roster answers, else
    ``degraded``."""
    cat = roster_catalog(client_factory=lambda n: client_for(n, timeout=2.0))
    cat["status"] = "ok" if not cat["unreachable"] else "degraded"
    return cat


@app.get("/portal/services")
def services() -> dict[str, Any]:
    """API catalog from the generated docs/api-catalog/*.openapi.json (if present)."""
    out: dict[str, list[str]] = {}
    for path in sorted(glob.glob(os.path.join(_catalog_dir(), "*.openapi.json"))):
        service = os.path.basename(path).replace(".openapi.json", "")
        try:
            with open(path) as f:
                spec = json.load(f)
        except Exception:
            continue
        routes = []
        for p, ops in sorted(spec.get("paths", {}).items()):
            if p in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
                continue
            for method in ops:
                if method.lower() in ("get", "post", "put", "patch", "delete"):
                    routes.append(f"{method.upper():6} {p}")
        out[service] = routes
    return {"services": out, "count": len(out)}
