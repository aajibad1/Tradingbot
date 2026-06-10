"""routing-service — resolve the best provider/corridor for a transfer (docs/06).

Given a direction (onramp/offramp), corridor (source→dest), and amount, returns a
ranked set of provider options and a chosen best route, by objective (lowest cost
or fastest settlement). Advisory only — moves no money; the on/off-ramp
orchestrators would call this to pick a provider. Resolved routes are stored so a
caller can reference a route_id when placing the order.

Endpoints (docs/06):
  GET  /healthz
  POST /v1/routes/resolve     {direction, source, dest, amount, objective?}
  GET  /v1/routes/{id}
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

import catalog
from shared.http import APIError, install_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("routing-service")

PRODUCER = "routing-service"

_routes: dict[str, dict] = {}

app = FastAPI(title="routing-service", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResolveRequest(BaseModel):
    direction: str = Field(description="onramp | offramp")
    source: str = Field(description="Source currency/asset")
    dest: str = Field(description="Destination currency/asset")
    amount: float = Field(gt=0)
    objective: str = Field(default=catalog.OBJECTIVE_COST, description="cost | speed")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/routes/resolve")
def resolve(req: ResolveRequest) -> dict[str, Any]:
    if req.direction not in ("onramp", "offramp"):
        raise APIError("invalid_direction", "direction must be onramp|offramp", http_status=422)
    if req.objective not in (catalog.OBJECTIVE_COST, catalog.OBJECTIVE_SPEED):
        raise APIError("invalid_objective", "objective must be cost|speed", http_status=422)
    options = catalog.resolve(req.direction, req.source, req.dest, req.amount, req.objective)
    if not options:
        raise APIError(
            "route_unavailable",
            f"no route available for {req.direction} {req.source}->{req.dest}",
            http_status=409,
        )
    route = {
        "id": f"rt_{uuid.uuid4().hex[:16]}",
        "direction": req.direction,
        "source": req.source,
        "dest": req.dest,
        "amount": req.amount,
        "objective": req.objective,
        "chosen": options[0],
        "alternatives": options[1:],
        "resolved_at": _now().isoformat(),
    }
    _routes[route["id"]] = route
    logger.info("resolved route %s %s %s->%s via %s", route["id"], req.direction,
                req.source, req.dest, options[0]["provider_id"])
    return route


@app.get("/v1/routes/{route_id}")
def get_route(route_id: str) -> dict[str, Any]:
    route = _routes.get(route_id)
    if route is None:
        raise APIError("route_not_found", f"route {route_id} not found", http_status=404)
    return route
