"""Tests for the API-catalog generator's pure logic.

Run: PYTHONPATH=.:scripts python3 -m pytest scripts/tests/test_api_catalog.py -v

The full generator spawns one subprocess per service (slow + needs every service's
deps); these cover the schema-shaping logic that turns an OpenAPI dict into the
catalog index, which is where the bugs would be.
"""
from __future__ import annotations

import build_api_catalog as cat


def _spec() -> dict:
    return {
        "paths": {
            "/healthz": {"get": {"summary": "Healthz"}},
            "/v1/onramp/orders": {
                "post": {"summary": "Create Order"},
                "get": {"summary": "List Orders"},
            },
            "/openapi.json": {"get": {"summary": "should be skipped"}},
            "/docs": {"get": {"summary": "should be skipped"}},
        }
    }


def test_routes_of_skips_framework_paths():
    rows = cat.routes_of(_spec())
    paths = {p for _m, p, _s in rows}
    assert "/openapi.json" not in paths and "/docs" not in paths
    assert "/healthz" in paths and "/v1/onramp/orders" in paths


def test_routes_of_extracts_method_and_summary():
    rows = cat.routes_of(_spec())
    by = {(m, p): s for m, p, s in rows}
    assert by[("POST", "/v1/onramp/orders")] == "Create Order"
    assert by[("GET", "/v1/onramp/orders")] == "List Orders"


def test_routes_of_falls_back_to_description_first_line():
    spec = {"paths": {"/x": {"get": {"description": "First line.\nSecond line."}}}}
    rows = cat.routes_of(spec)
    assert rows == [("GET", "/x", "First line.")]


def test_services_list_matches_api_plane():
    # Guard against drift: the catalog should enumerate exactly the API-plane svcs.
    assert "public-api-gateway" in cat.SERVICES
    assert "partner-auth" in cat.SERVICES
    assert len(cat.SERVICES) == 10
