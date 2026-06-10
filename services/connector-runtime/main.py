"""connector-runtime — pluggable connector registry + health (docs/03 shared-core).

The shared seam both businesses depend on: every venue (exchange, DEX, stablecoin
rail, FX feed) registers here and reports health, so the platform reads one catalog
instead of knowing vendors. This is the registry/health read model — it holds no
credentials and does no venue I/O; real adapters (CCXT, native, rails) report into it.

Endpoints:
  GET  /healthz
  POST /v1/connectors                    register {venue, region, market_type, kind, provider?}
  GET  /v1/connectors?region=&market_type=&status=
  GET  /v1/connectors/{venue}
  POST /v1/connectors/{venue}/health     {healthy, latency_ms?, detail?}
  POST /v1/connectors/{venue}/disable
  GET  /v1/connectors/health             aggregate summary
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

import registry as reg
from shared.http import APIError, install_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("connector-runtime")

PRODUCER = "connector-runtime"

_registry = reg.ConnectorRegistry()

app = FastAPI(title="connector-runtime", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectorRegister(BaseModel):
    venue: str
    region: str = Field(description="global | africa")
    market_type: str = Field(description="cex | dex | rail | fx")
    kind: str = Field(description="cex | dex | rail | fx | reference")
    provider: str = ""


class HealthReport(BaseModel):
    healthy: bool
    latency_ms: int | None = None
    detail: str = ""


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/connectors")
def register(req: ConnectorRegister) -> dict[str, Any]:
    if req.venue in _registry._c:
        raise APIError("connector_exists", f"connector {req.venue} already registered", http_status=409)
    try:
        return _registry.register(req.venue, req.region, req.market_type, req.kind, req.provider)
    except ValueError as e:
        raise APIError("invalid_connector", str(e), http_status=422)


@app.get("/v1/connectors")
def list_connectors(region: str | None = None, market_type: str | None = None,
                    status: str | None = None) -> dict[str, Any]:
    items = _registry.list(region=region, market_type=market_type, status=status)
    return {"connectors": items, "count": len(items)}


@app.get("/v1/connectors/health")
def health_summary() -> dict[str, Any]:
    return _registry.summary()


def _get(venue: str) -> dict[str, Any]:
    try:
        return _registry.get(venue)
    except KeyError:
        raise APIError("connector_not_found", f"connector {venue} not found", http_status=404)


@app.get("/v1/connectors/{venue}")
def get_connector(venue: str) -> dict[str, Any]:
    return _get(venue)


@app.post("/v1/connectors/{venue}/health")
def report_health(venue: str, req: HealthReport) -> dict[str, Any]:
    _get(venue)
    return _registry.report_health(venue, req.healthy, req.latency_ms, req.detail, _now())


@app.post("/v1/connectors/{venue}/disable")
def disable(venue: str) -> dict[str, Any]:
    _get(venue)
    return _registry.disable(venue)
