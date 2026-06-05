"""core-api tests — local auth mode, SQLite (no Firebase/Postgres needed).

Run: PYTHONPATH=.:services/core-api python3 -m pytest services/core-api/tests/ -v
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from shared.tenant import validate_tenant


@pytest.fixture()
def client(monkeypatch):
    # Isolated SQLite file per test; local auth mode (no FIREBASE_PROJECT_ID).
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    import db
    import main
    db.init_db(f"sqlite:///{path}")  # rebuild engine against the temp file
    with TestClient(main.app) as c:
        yield c
    os.unlink(path)


def _auth(uid: str, email: str | None = None) -> dict[str, str]:
    tok = f"local:{uid}" + (f":{email}" if email else "")
    return {"Authorization": f"Bearer {tok}"}


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_session_mints_tenant_and_owner_role(client):
    r = client.post("/v1/sessions", headers=_auth("uid-1", "a@x.com"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == "uid-1"
    assert body["email"] == "a@x.com"
    assert body["roles"] == ["owner"]
    assert body["onboarding_status"] == "account_created"
    assert body["kyc_status"] == "none"
    assert body["live_enabled"] is False  # paper by default
    assert body["market"] is None          # set later at onboarding
    # tenant id must be a valid shared.tenant namespace fragment
    validate_tenant(body["tenant_id"])


def test_session_is_idempotent(client):
    a = client.post("/v1/sessions", headers=_auth("uid-2", "b@x.com")).json()
    b = client.post("/v1/sessions", headers=_auth("uid-2", "b@x.com")).json()
    assert a["tenant_id"] == b["tenant_id"]
    assert b["roles"] == ["owner"]  # not duplicated


def test_distinct_users_get_distinct_tenants(client):
    a = client.post("/v1/sessions", headers=_auth("uid-3")).json()
    b = client.post("/v1/sessions", headers=_auth("uid-4")).json()
    assert a["tenant_id"] != b["tenant_id"]


def test_me_404_before_session_then_200(client):
    assert client.get("/v1/me", headers=_auth("uid-5")).status_code == 404
    client.post("/v1/sessions", headers=_auth("uid-5", "c@x.com"))
    r = client.get("/v1/me", headers=_auth("uid-5"))
    assert r.status_code == 200
    assert r.json()["user_id"] == "uid-5"


def test_missing_auth_is_401(client):
    assert client.get("/v1/me").status_code == 401


# --- onboarding state machine -------------------------------------------------

def _onboard(client, uid, market, country):
    h = _auth(uid, f"{uid}@x.com")
    client.post("/v1/sessions", headers=h)
    client.post("/v1/onboarding/region", headers=h, json={"market": market, "country": country})
    client.post("/v1/onboarding/kyc", headers=h, json={"full_name": "Test User"})
    return client.post("/v1/onboarding/submit", headers=h).json()


def test_full_flow_africa_launch_country_reaches_trading_ready(client):
    out = _onboard(client, "ob-1", "africa", "NG")  # Nigeria — launch set
    assert out["onboarding_status"] == "trading_ready"
    assert out["market"] == "africa"
    assert out["region"] == "NG"
    assert "start_paper_trading" in out["next_actions"]


def test_africa_non_launch_country_goes_to_review(client):
    out = _onboard(client, "ob-2", "africa", "EG")  # Egypt — not in launch set
    assert out["onboarding_status"] == "review_pending"
    assert "corridor_review" in out["required_controls"]


def test_blocked_jurisdiction_is_restricted(client):
    out = _onboard(client, "ob-3", "global", "IR")  # sanctioned placeholder
    assert out["onboarding_status"] == "restricted"


def test_global_launch_reaches_trading_ready(client):
    out = _onboard(client, "ob-4", "global", "US")
    assert out["onboarding_status"] == "trading_ready"


def test_illegal_transition_is_409(client):
    h = _auth("ob-5", "e@x.com")
    client.post("/v1/sessions", headers=h)
    # submit before region/kyc — ACCOUNT_CREATED -> (policy) is illegal
    r = client.post("/v1/onboarding/submit", headers=h)
    assert r.status_code == 409


def test_onboarding_view_tracks_next_action(client):
    h = _auth("ob-6", "f@x.com")
    client.post("/v1/sessions", headers=h)
    v = client.get("/v1/onboarding", headers=h).json()
    assert v["onboarding_status"] == "account_created"
    assert v["next_actions"] == ["select_region"]


def test_local_mode_rejects_non_local_bearer(client):
    # a raw token (not 'local:...') is refused when no Firebase is configured
    r = client.get("/v1/me", headers={"Authorization": "Bearer some-random-jwt"})
    assert r.status_code == 401
