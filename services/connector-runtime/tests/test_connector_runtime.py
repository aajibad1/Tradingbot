"""connector-runtime: registry logic + register/health/summary endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
import registry as reg


# --- pure registry ---------------------------------------------------------- #


def test_register_validates_vocabulary():
    r = reg.ConnectorRegistry()
    r.register("kraken", "global", "cex", "cex", "ccxt")
    with pytest.raises(ValueError):
        r.register("bad", "mars", "cex", "cex")
    with pytest.raises(ValueError):
        r.register("bad", "global", "spot", "cex")
    with pytest.raises(ValueError):
        r.register("bad", "global", "cex", "wormhole")


def test_health_drives_status():
    r = reg.ConnectorRegistry()
    r.register("valr", "africa", "rail", "rail", "valr")
    assert r.report_health("valr", True, 50, "", "t0")["status"] == reg.ACTIVE
    assert r.report_health("valr", True, 2000, "", "t1")["status"] == reg.DEGRADED  # slow
    assert r.report_health("valr", False, None, "timeout", "t2")["status"] == reg.DOWN


def test_disabled_ignores_health():
    r = reg.ConnectorRegistry()
    r.register("luno", "africa", "cex", "cex")
    r.disable("luno")
    assert r.report_health("luno", True, 10, "", "t0")["status"] == reg.DISABLED


def test_summary_counts():
    r = reg.ConnectorRegistry()
    r.register("kraken", "global", "cex", "cex")
    r.register("valr", "africa", "rail", "rail")
    r.report_health("valr", False, None, "down", "t0")
    s = r.summary()
    assert s["total"] == 2
    assert s["by_region"] == {"global": 1, "africa": 1}
    assert "valr" in s["unhealthy"]


# --- endpoints -------------------------------------------------------------- #


@pytest.fixture()
def client():
    main._registry = reg.ConnectorRegistry()
    return TestClient(main.app)


def _reg(client, venue="kraken", region="global", market_type="cex", kind="cex"):
    return client.post("/v1/connectors", json={"venue": venue, "region": region,
                                                "market_type": market_type, "kind": kind})


def test_register_and_get(client):
    assert _reg(client).status_code == 200
    assert client.get("/v1/connectors/kraken").json()["region"] == "global"


def test_duplicate_409(client):
    _reg(client)
    assert _reg(client).status_code == 409


def test_invalid_vocabulary_422(client):
    r = _reg(client, region="atlantis")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_connector"


def test_health_report_and_filtering(client):
    _reg(client, venue="kraken", region="global")
    _reg(client, venue="valr", region="africa", market_type="rail", kind="rail")
    client.post("/v1/connectors/valr/health", json={"healthy": False, "detail": "timeout"})
    down = client.get("/v1/connectors", params={"status": "down"}).json()
    assert down["count"] == 1 and down["connectors"][0]["venue"] == "valr"
    africa = client.get("/v1/connectors", params={"region": "africa"}).json()
    assert africa["count"] == 1


def test_health_summary_endpoint(client):
    _reg(client, venue="kraken")
    _reg(client, venue="valr", region="africa", market_type="rail", kind="rail")
    client.post("/v1/connectors/valr/health", json={"healthy": True, "latency_ms": 3000})
    s = client.get("/v1/connectors/health").json()
    assert s["total"] == 2 and "valr" in s["unhealthy"]  # degraded counts as unhealthy


def test_get_missing_404(client):
    assert client.get("/v1/connectors/ghost").status_code == 404
    assert client.post("/v1/connectors/ghost/health", json={"healthy": True}).status_code == 404
