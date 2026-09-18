"""wallet-service: wallets, overdraft-protected adjust, aggregated balances.

Balances are Decimal internally and serialize as strings on the wire
(docs/adr/0004-money-representation.md) — FastAPI's default JSON encoder
would silently cast Decimal -> float otherwise, which is exactly the bug
this service used to have.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client():
    main._wallets.clear()
    return TestClient(main.app)


def _wallet(client, tenant="ten_1", asset="USDC"):
    return client.post("/v1/wallets", json={"tenant_id": tenant, "asset": asset}).json()


def test_create_wallet_starts_at_zero(client):
    w = _wallet(client)
    assert w["id"].startswith("wlt_")
    assert w["balance"] == "0"
    assert w["asset"] == "USDC"


def test_credit_then_debit(client):
    wid = _wallet(client)["id"]
    assert client.post(f"/v1/wallets/{wid}/adjust", json={"amount": "99.0", "reason": "funding"}).json()["balance"] == "99.00000000"
    assert client.post(f"/v1/wallets/{wid}/adjust", json={"amount": "-40.0", "reason": "payout"}).json()["balance"] == "59.00000000"


def test_overdraft_is_blocked(client):
    wid = _wallet(client)["id"]
    client.post(f"/v1/wallets/{wid}/adjust", json={"amount": "10.0"})
    r = client.post(f"/v1/wallets/{wid}/adjust", json={"amount": "-25.0"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "insufficient_funds"
    # balance unchanged after the rejected debit
    assert client.get(f"/v1/wallets/{wid}").json()["balance"] == "10.00000000"


def test_zero_amount_rejected(client):
    wid = _wallet(client)["id"]
    r = client.post(f"/v1/wallets/{wid}/adjust", json={"amount": "0"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_amount"


def test_adjust_missing_wallet_404(client):
    r = client.post("/v1/wallets/wlt_nope/adjust", json={"amount": "1.0"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "wallet_not_found"


def test_balances_aggregate_per_asset_across_wallets(client):
    a = _wallet(client, asset="USDC")["id"]
    b = _wallet(client, asset="USDC")["id"]
    c = _wallet(client, asset="NGN")["id"]
    client.post(f"/v1/wallets/{a}/adjust", json={"amount": "50.0"})
    client.post(f"/v1/wallets/{b}/adjust", json={"amount": "25.5"})
    client.post(f"/v1/wallets/{c}/adjust", json={"amount": "100000.0"})
    bal = client.get("/v1/balances", params={"tenant": "ten_1"}).json()["balances"]
    assert bal == {"USDC": "75.50000000", "NGN": "100000.00000000"}


def test_list_wallets_filtered_by_tenant(client):
    _wallet(client, tenant="ten_1")
    _wallet(client, tenant="ten_2")
    assert client.get("/v1/wallets", params={"tenant": "ten_1"}).json()["count"] == 1
    assert client.get("/v1/wallets").json()["count"] == 2


# --- Decimal-migration regression coverage (docs/adr/0004, issue #20) ------ #


def test_json_float_amount_is_rejected_not_silently_accepted(client):
    """A JSON float (99.1) has already lost precision by the time Python's
    json module hands it to Pydantic as a float object — accepting it would
    reintroduce exactly the bug this migration closes. Only string/int
    amounts are accepted."""
    wid = _wallet(client)["id"]
    r = client.post(f"/v1/wallets/{wid}/adjust", json={"amount": 99.1})
    assert r.status_code == 422


def test_precision_survives_many_small_postings(client):
    """A case float rounding would corrupt: 0.1 + 0.1 + 0.1 != 0.3 in binary
    float, but is exact in Decimal."""
    wid = _wallet(client)["id"]
    for _ in range(3):
        resp = client.post(f"/v1/wallets/{wid}/adjust", json={"amount": "0.1"})
    assert resp.json()["balance"] == "0.30000000"
    assert Decimal(resp.json()["balance"]) == Decimal("0.3")


def test_balance_response_is_a_string_never_a_json_number(client):
    """Guards against FastAPI's default encoder silently casting Decimal
    back to float on the wire — the exact regression this migration fixes."""
    w = _wallet(client)
    assert isinstance(w["balance"], str)
