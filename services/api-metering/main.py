"""api-metering — per-tenant API usage metering for billing (docs/05, docs/06).

Other API-plane services report usage here (after authenticating via partner-auth);
this aggregates per tenant per monthly period, broken down by route, and flags
overages for metered billing. Billing unit = tenant_id.

Endpoints (docs/06):
  GET  /healthz
  POST /v1/metering/record       {tenant_id, route, count?, period?, api_key_id?}
  GET  /v1/metering/usage?tenant=&period=
  POST /v1/metering/quota        {tenant_id, monthly_limit}
  GET  /v1/metering/quota?tenant=
  GET  /v1/metering/check?tenant=&count=    pre-flight: would this exceed quota?
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from meter import Meter, current_period
from shared.http import APIError, install_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("api-metering")

PRODUCER = "api-metering"

_meter = Meter()

app = FastAPI(title="api-metering", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecordRequest(BaseModel):
    tenant_id: str
    route: str = Field(description="Logical route, e.g. 'POST /v1/onramp/orders'")
    count: int = Field(default=1, gt=0)
    period: str | None = Field(default=None, description="Override billing period (default: now)")
    api_key_id: str | None = Field(default=None, description="Attribution only, not the billing key")


class QuotaRequest(BaseModel):
    tenant_id: str
    monthly_limit: int = Field(gt=0)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/metering/record")
def record(req: RecordRequest) -> dict[str, Any]:
    period = req.period or current_period(_now())
    usage = _meter.record(req.tenant_id, req.route, period, req.count)
    if usage["over_quota"]:
        logger.warning("tenant=%s OVER QUOTA period=%s total=%d limit=%s",
                       req.tenant_id, period, usage["total"], usage["quota"])
    return usage


@app.get("/v1/metering/usage")
def usage(tenant: str, period: str | None = None) -> dict[str, Any]:
    return _meter.usage(tenant, period or current_period(_now()))


@app.post("/v1/metering/quota")
def set_quota(req: QuotaRequest) -> dict[str, Any]:
    _meter.set_quota(req.tenant_id, req.monthly_limit)
    return {"tenant_id": req.tenant_id, "monthly_limit": req.monthly_limit}


@app.get("/v1/metering/quota")
def get_quota(tenant: str) -> dict[str, Any]:
    limit = _meter.get_quota(tenant)
    if limit is None:
        raise APIError("quota_not_set", f"no quota set for tenant {tenant}", http_status=404)
    return {"tenant_id": tenant, "monthly_limit": limit}


@app.get("/v1/metering/check")
def check(tenant: str, count: int = 1, period: str | None = None) -> dict[str, Any]:
    period = period or current_period(_now())
    return {
        "tenant_id": tenant,
        "period": period,
        "count": count,
        "would_exceed": _meter.would_exceed(tenant, period, count),
    }
