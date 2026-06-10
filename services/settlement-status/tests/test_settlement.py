"""settlement-status: projection logic + ingest/query endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
import projection
from shared.http import Status
from shared.models.event_envelope import EventEnvelope


def _funding_env(event_type: str, order_id: str = "ord_1", tenant: str = "ten_1") -> dict:
    return EventEnvelope.wrap(
        event_type=event_type,
        payload={"id": order_id, "source_currency": "NGN", "dest_asset": "USDC",
                 "amount": 160000.0, "dest_amount": 99.0},
        producer="onramp-orchestrator", tenant_id=tenant,
    ).model_dump(mode="json")


def _payout_env(event_type: str, order_id: str = "pay_1") -> dict:
    return EventEnvelope.wrap(
        event_type=event_type,
        payload={"id": order_id, "source_asset": "USDC", "dest_currency": "NGN",
                 "amount": 100.0, "dest_amount": 157600.0},
        producer="offramp-orchestrator", tenant_id="ten_1",
    ).model_dump(mode="json")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    main._settlements.clear()
    return TestClient(main.app)


# --- pure projection -------------------------------------------------------- #


def test_status_and_kind_mapping():
    assert projection.status_for("funding.created") == Status.PENDING
    assert projection.status_for("payout.completed") == Status.COMPLETED
    assert projection.kind_for("funding.processing") == "onramp"
    assert projection.kind_for("payout.failed") == "offramp"
    assert projection.status_for("market.tick.btc") is None  # not a settlement event


def test_project_builds_then_updates_record():
    r1 = projection.project(None, _funding_env("funding.created"))
    assert r1["status"] == Status.PENDING and r1["kind"] == "onramp"
    assert r1["source"] == "NGN" and r1["dest"] == "USDC"
    assert len(r1["history"]) == 1
    r2 = projection.project(r1, _funding_env("funding.completed"))
    assert r2["status"] == Status.COMPLETED
    assert len(r2["history"]) == 2


def test_project_ignores_non_settlement_events():
    assert projection.project(None, {"event_type": "audit.something", "payload": {"id": "x"}}) is None
    assert projection.project(None, _funding_env("funding.created", order_id="")) is None


# --- endpoints -------------------------------------------------------------- #


def test_ingest_then_query_reflects_latest_status(client):
    client.post("/v1/events/ingest", json=_funding_env("funding.created"))
    client.post("/v1/events/ingest", json=_funding_env("funding.processing"))
    r = client.post("/v1/events/ingest", json=_funding_env("funding.completed"))
    assert r.json()["ingested"] is True

    rec = client.get("/v1/settlements/ord_1").json()
    assert rec["status"] == Status.COMPLETED
    assert rec["kind"] == "onramp"
    assert len(rec["history"]) == 3


def test_ingest_non_settlement_event_is_ignored(client):
    r = client.post("/v1/events/ingest", json={"event_type": "auth.login", "payload": {"id": "u1"}})
    assert r.status_code == 200 and r.json()["ingested"] is False


def test_get_missing_settlement_is_404(client):
    r = client.get("/v1/settlements/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "settlement_not_found"


def test_list_filters_by_status_and_tenant(client):
    client.post("/v1/events/ingest", json=_funding_env("funding.completed", order_id="ord_a"))
    client.post("/v1/events/ingest", json=_payout_env("payout.created", order_id="pay_b"))

    done = client.get("/v1/settlements", params={"status": Status.COMPLETED}).json()
    assert done["count"] == 1 and done["settlements"][0]["id"] == "ord_a"

    all_t1 = client.get("/v1/settlements", params={"tenant": "ten_1"}).json()
    assert all_t1["count"] == 2  # both belong to ten_1; one onramp + one offramp


def test_correlation_headers_present(client):
    from shared.http import CORRELATION_ID_HEADER
    assert client.get("/healthz").headers[CORRELATION_ID_HEADER].startswith("corr_")
