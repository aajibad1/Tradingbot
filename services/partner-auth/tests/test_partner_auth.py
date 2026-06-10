"""partner-auth: key logic + issue/verify/rotate/revoke + prod-key gating."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import keystore
import main


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("PARTNER_PROD_KEYS_ENABLED", raising=False)
    main._keys.clear()
    return TestClient(main.app)


# --- pure keystore ---------------------------------------------------------- #


def test_token_roundtrip_and_hash_never_reversible():
    key_id = keystore.new_key_id("sandbox")
    secret = keystore.new_secret()
    token = keystore.make_token(key_id, secret)
    assert keystore.parse_token(token) == (key_id, secret)
    assert keystore.env_of(key_id) == "sandbox"
    h = keystore.hash_secret(secret)
    assert h != secret and keystore.secret_matches(secret, h)
    assert not keystore.secret_matches("wrong", h)


def test_parse_rejects_malformed():
    assert keystore.parse_token("") is None
    assert keystore.parse_token("no-dot") is None
    assert keystore.parse_token("notpk_x.secret") is None


# --- issue + verify --------------------------------------------------------- #


def test_issue_returns_token_once_and_list_hides_secret(client):
    r = client.post("/v1/partner/keys", json={"tenant_id": "ten_1", "scopes": ["onramp:write"]})
    issued = r.json()
    assert issued["token"].startswith("pk_sandbox_") and "." in issued["token"]

    listed = client.get("/v1/partner/keys").json()["keys"]
    assert len(listed) == 1
    assert "secret_hash" not in listed[0] and "token" not in listed[0]


def test_verify_valid_and_invalid_paths(client):
    token = client.post("/v1/partner/keys",
                        json={"tenant_id": "ten_9", "scopes": ["x"]}).json()["token"]
    ok = client.post("/v1/partner/keys/verify", json={"token": token}).json()
    assert ok["valid"] and ok["tenant_id"] == "ten_9" and ok["scopes"] == ["x"]

    bad = client.post("/v1/partner/keys/verify", json={"token": "pk_sandbox_x.deadbeef"}).json()
    assert bad["valid"] is False and bad["reason"] == "unknown_key"

    malformed = client.post("/v1/partner/keys/verify", json={"token": "garbage"}).json()
    assert malformed["valid"] is False and malformed["reason"] == "malformed_token"


def test_rotate_invalidates_old_secret(client):
    issued = client.post("/v1/partner/keys", json={"tenant_id": "ten_1"}).json()
    old_token, key_id = issued["token"], issued["key_id"]
    new_token = client.post(f"/v1/partner/keys/{key_id}/rotate").json()["token"]
    assert new_token != old_token
    assert client.post("/v1/partner/keys/verify", json={"token": new_token}).json()["valid"] is True
    # old secret no longer matches the rotated hash
    assert client.post("/v1/partner/keys/verify", json={"token": old_token}).json()["reason"] == "bad_secret"


def test_revoke_blocks_verification(client):
    issued = client.post("/v1/partner/keys", json={"tenant_id": "ten_1"}).json()
    client.post(f"/v1/partner/keys/{issued['key_id']}/revoke")
    res = client.post("/v1/partner/keys/verify", json={"token": issued["token"]}).json()
    assert res["valid"] is False and res["reason"] == "revoked"


# --- production-key gating --------------------------------------------------- #


def test_production_keys_blocked_by_default(client):
    r = client.post("/v1/partner/keys", json={"tenant_id": "ten_1", "environment": "production"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "production_keys_disabled"


def test_production_keys_allowed_when_enabled(client, monkeypatch):
    monkeypatch.setenv("PARTNER_PROD_KEYS_ENABLED", "true")
    r = client.post("/v1/partner/keys", json={"tenant_id": "ten_1", "environment": "production"})
    assert r.status_code == 200
    assert r.json()["token"].startswith("pk_production_")


def test_invalid_environment_rejected(client):
    r = client.post("/v1/partner/keys", json={"tenant_id": "ten_1", "environment": "staging"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_environment"
