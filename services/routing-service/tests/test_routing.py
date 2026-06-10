"""routing-service: catalog resolution + resolve/get endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import catalog
import main


# --- pure catalog ----------------------------------------------------------- #


def test_cost_objective_prefers_cheapest_covering_provider():
    # NGN->USDC is covered by sandbox-cheap (55 bps) → it wins on cost.
    opts = catalog.resolve("onramp", "NGN", "USDC", 1000.0, catalog.OBJECTIVE_COST)
    assert opts[0]["provider_id"] == "sandbox-cheap"
    assert opts[0]["fee_bps"] == 55.0


def test_speed_objective_prefers_fastest():
    opts = catalog.resolve("onramp", "NGN", "USDC", 1000.0, catalog.OBJECTIVE_SPEED)
    assert opts[0]["provider_id"] == "sandbox-fast"
    assert opts[0]["estimated_settlement_seconds"] == 30


def test_uncovered_corridor_falls_back_to_general_providers():
    # GHS->USDC isn't in sandbox-cheap's corridors → only fast/balanced match.
    opts = catalog.resolve("onramp", "GHS", "USDC", 1000.0, catalog.OBJECTIVE_COST)
    ids = {o["provider_id"] for o in opts}
    assert ids == {"sandbox-fast", "sandbox-balanced"}
    assert opts[0]["provider_id"] == "sandbox-balanced"  # cheaper of the two


def test_fee_scales_with_amount():
    opts = catalog.resolve("offramp", "USDC", "NGN", 200.0, catalog.OBJECTIVE_COST)
    best = opts[0]
    assert best["estimated_fee"] == round(200.0 * best["fee_bps"] / 10_000, 6)


# --- endpoints -------------------------------------------------------------- #


@pytest.fixture()
def client():
    main._routes.clear()
    return TestClient(main.app)


def test_resolve_returns_chosen_and_alternatives(client):
    r = client.post("/v1/routes/resolve", json={
        "direction": "onramp", "source": "NGN", "dest": "USDC", "amount": 1000.0,
    })
    body = r.json()
    assert body["id"].startswith("rt_")
    assert body["chosen"]["provider_id"] == "sandbox-cheap"
    assert len(body["alternatives"]) == 2  # fast + balanced


def test_resolve_then_get(client):
    rid = client.post("/v1/routes/resolve", json={
        "direction": "offramp", "source": "USDC", "dest": "NGN", "amount": 50.0,
    }).json()["id"]
    got = client.get(f"/v1/routes/{rid}").json()
    assert got["id"] == rid and got["direction"] == "offramp"


def test_invalid_direction_422(client):
    r = client.post("/v1/routes/resolve", json={
        "direction": "sideways", "source": "NGN", "dest": "USDC", "amount": 10.0,
    })
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_direction"


def test_get_missing_route_404(client):
    r = client.get("/v1/routes/rt_missing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "route_not_found"
