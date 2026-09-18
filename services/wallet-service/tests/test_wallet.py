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
    assert w["balance"] == "0.00000000"
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


def test_sub_micro_unit_balance_stays_fixed_point_not_scientific_notation(client):
    """Decimal.__str__ switches to scientific notation below 1e-6 (e.g.
    Decimal("0.00000002") -> "2E-8") — exactly the sub-micro-unit range a
    stablecoin balance can legitimately sit in, and exactly what
    _wallet_out's format(x, "f") exists to prevent. Caught by independent
    review: str() passed every other test here because none used an amount
    small enough to trigger it."""
    wid = _wallet(client)["id"]
    r = client.post(f"/v1/wallets/{wid}/adjust", json={"amount": "0.00000002"})
    assert r.json()["balance"] == "0.00000002"
    assert "E" not in r.json()["balance"] and "e" not in r.json()["balance"]


def test_non_numeric_amount_returns_the_standard_error_envelope(client):
    """A Pydantic validation failure (not a business-rule APIError) must
    still come back as {"error": {code, message, ...}} per docs/06 — not
    FastAPI's default {"detail": [...]}. Caught by independent review."""
    wid = _wallet(client)["id"]
    r = client.post(f"/v1/wallets/{wid}/adjust", json={"amount": "not-a-number"})
    assert r.status_code == 422
    body = r.json()
    assert "error" in body and "detail" not in body
    assert body["error"]["code"] == "validation_error"


def test_float_rejection_also_uses_the_standard_error_envelope(client):
    """Same envelope requirement for the amount-is-a-JSON-float rejection
    specifically (the field_validator path), not just generic type errors."""
    wid = _wallet(client)["id"]
    r = client.post(f"/v1/wallets/{wid}/adjust", json={"amount": 99.1})
    body = r.json()
    assert "error" in body and "detail" not in body


def test_absurdly_large_amount_is_a_clean_422_not_a_500(client):
    """Decimal's default context precision (28 significant digits) is a real
    ceiling — confirm it fails closed as invalid_amount, not an unhandled
    InvalidOperation surfacing as a 500."""
    wid = _wallet(client)["id"]
    r = client.post(f"/v1/wallets/{wid}/adjust", json={"amount": "1e20"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_amount"
