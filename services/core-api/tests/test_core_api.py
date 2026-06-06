"""core-api tests — local auth mode, SQLite (no Firebase/Postgres needed).

Run: PYTHONPATH=.:services/core-api python3 -m pytest services/core-api/tests/ -v
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from shared.tenant import validate_tenant

_WHSEC = "whsec_test"
# Svix secret must be whsec_<base64>; this decodes cleanly.
_CLERK_SECRET = "whsec_" + __import__("base64").b64encode(b"clerk-test-key").decode()


@pytest.fixture()
def client(monkeypatch):
    # Isolated SQLite file per test; local auth mode (no FIREBASE_PROJECT_ID).
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", _CLERK_SECRET)
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


# --- billing / entitlements ---------------------------------------------------

def _mint(client, uid) -> str:
    return client.post("/v1/sessions", headers=_auth(uid, f"{uid}@x.com")).json()["tenant_id"]


def _sign(payload: bytes, t: str = "1700000000", secret: str = _WHSEC) -> str:
    sig = hmac.new(secret.encode(), t.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def _event(etype, tenant_id, plan=None, event_id="evt_1", customer="cus_1") -> dict:
    obj = {"customer": customer, "metadata": {"tenant_id": tenant_id}}
    if plan:
        obj["metadata"]["plan"] = plan
    return {"id": event_id, "type": etype, "data": {"object": obj}}

def _post_event(client, ev, sign_secret: str = _WHSEC):
    payload = json.dumps(ev).encode()
    return client.post("/webhooks/stripe", content=payload,
                       headers={"Stripe-Signature": _sign(payload, secret=sign_secret)})


def test_default_entitlements_are_free_paper_only(client):
    h = _auth("bil-1", "g@x.com")
    client.post("/v1/sessions", headers=h)
    ent = client.get("/v1/entitlements", headers=h).json()
    assert ent["plan"] == "free"
    assert ent["paper_trading"] is True
    assert ent["live_trading"] is False
    assert ent["markets"] == []


def test_pro_subscription_unlocks_live_and_both_markets(client):
    tid = _mint(client, "bil-2")
    r = _post_event(client, _event("checkout.session.completed", tid, plan="pro"))
    assert r.status_code == 200, r.text
    ent = client.get("/v1/entitlements", headers=_auth("bil-2")).json()
    assert ent["plan"] == "pro"
    assert ent["subscription_status"] == "active"
    assert ent["live_trading"] is True
    assert set(ent["markets"]) == {"global", "africa"}
    assert ent["max_live_capital_usd"] == 100000.0


def test_invalid_signature_is_rejected(client):
    tid = _mint(client, "bil-3")
    r = _post_event(client, _event("checkout.session.completed", tid, plan="pro"),
                    sign_secret="wrong-secret")
    assert r.status_code == 400
    # plan unchanged
    assert client.get("/v1/entitlements", headers=_auth("bil-3")).json()["plan"] == "free"


def test_webhook_is_idempotent(client):
    tid = _mint(client, "bil-4")
    ev = _event("checkout.session.completed", tid, plan="pro", event_id="evt_dup")
    assert _post_event(client, ev).json()["status"] == "ok"
    assert _post_event(client, ev).json()["status"] == "duplicate"
    assert client.get("/v1/entitlements", headers=_auth("bil-4")).json()["plan"] == "pro"


def test_subscription_deleted_downgrades_to_free(client):
    tid = _mint(client, "bil-5")
    _post_event(client, _event("checkout.session.completed", tid, plan="pro", event_id="e1"))
    _post_event(client, _event("customer.subscription.deleted", tid, event_id="e2"))
    ent = client.get("/v1/entitlements", headers=_auth("bil-5")).json()
    assert ent["plan"] == "free"
    assert ent["subscription_status"] == "canceled"


def test_webhook_503_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    tid = _mint(client, "bil-6")
    r = _post_event(client, _event("checkout.session.completed", tid, plan="pro"))
    assert r.status_code == 503


# --- RBAC + live-trading gate -------------------------------------------------

def test_rbac_role_permission_mapping():
    from db import Role
    from rbac import Permission, effective_permissions
    assert Permission.ENABLE_LIVE_TRADING in effective_permissions([Role.OWNER])
    assert Permission.ENABLE_LIVE_TRADING not in effective_permissions([Role.ANALYST])
    # withdrawal is owner-only, even for admin
    assert Permission.REQUEST_WITHDRAWAL in effective_permissions([Role.OWNER])
    assert Permission.REQUEST_WITHDRAWAL not in effective_permissions([Role.ADMIN])


def test_owner_has_all_permissions(client):
    h = _auth("rb-1", "h@x.com")
    client.post("/v1/sessions", headers=h)
    body = client.get("/v1/permissions", headers=h).json()
    assert body["roles"] == ["owner"]
    assert "enable_live_trading" in body["permissions"]
    assert "request_withdrawal" in body["permissions"]


def _ready_pro(client, uid, market="global", country="US"):
    h = _auth(uid, f"{uid}@x.com")
    tid = client.post("/v1/sessions", headers=h).json()["tenant_id"]
    client.post("/v1/onboarding/region", headers=h, json={"market": market, "country": country})
    client.post("/v1/onboarding/kyc", headers=h, json={"full_name": "T"})
    client.post("/v1/onboarding/submit", headers=h)
    _post_event(client, _event("checkout.session.completed", tid, plan="pro", event_id=f"evt-{uid}"))
    return h


def test_live_enable_blocked_without_pro_plan(client):
    # onboarded to trading_ready but still on the free plan
    h = _auth("le-1", "i@x.com")
    client.post("/v1/sessions", headers=h)
    client.post("/v1/onboarding/region", headers=h, json={"market": "global", "country": "US"})
    client.post("/v1/onboarding/kyc", headers=h, json={"full_name": "T"})
    client.post("/v1/onboarding/submit", headers=h)
    r = client.post("/v1/trading/live-enable", headers=h)
    assert r.status_code == 403  # plan gate


def test_live_enable_blocked_until_trading_ready(client):
    # pro plan but onboarding not completed
    tid = _mint(client, "le-2")
    _post_event(client, _event("checkout.session.completed", tid, plan="pro", event_id="evt-le2"))
    r = client.post("/v1/trading/live-enable", headers=_auth("le-2"))
    assert r.status_code == 409  # onboarding gate


def test_live_enable_succeeds_when_all_gates_pass(client):
    h = _ready_pro(client, "le-3")
    r = client.post("/v1/trading/live-enable", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["live_enabled"] is True


# --- audit trail --------------------------------------------------------------

def test_audit_trail_records_key_actions(client):
    h = _ready_pro(client, "au-1")              # session + onboarding + billing
    client.post("/v1/trading/live-enable", headers=h)
    entries = client.get("/v1/audit", headers=h).json()
    actions = [e["action"] for e in entries]
    # newest first; the lifecycle is captured
    assert "live.enabled" in actions
    assert "kyc.verified" in actions
    assert "account.created" in actions
    assert actions[0] == "live.enabled"         # most recent on top
    # every entry has an actor
    assert all(e["actor"] for e in entries)


def test_audit_requires_permission(client):
    # downgrade the user to OPERATOR (lacks VIEW_AUDIT_LOG) and confirm 403
    import db as dbmod
    from db import Role
    h = _auth("au-2", "k@x.com")
    client.post("/v1/sessions", headers=h)
    session = next(dbmod.get_session())
    ur = session.query(dbmod.UserRole).filter_by(user_id="au-2").one()
    ur.role = Role.OPERATOR
    session.commit()
    session.close()
    assert client.get("/v1/audit", headers=h).status_code == 403


# --- Clerk webhook sync -------------------------------------------------------

def _clerk_headers(payload: bytes, svix_id="msg_1", ts="1700000000", secret=_CLERK_SECRET):
    import base64
    key = base64.b64decode(secret.split("_", 1)[1])
    signed = f"{svix_id}.{ts}.".encode() + payload
    sig = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return {"svix-id": svix_id, "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}


def _clerk_event(etype, uid, email=None):
    data = {"id": uid}
    if email:
        data["email_addresses"] = [{"id": "e1", "email_address": email}]
        data["primary_email_address_id"] = "e1"
    return {"type": etype, "data": data}


def _post_clerk(client, ev, svix_id="msg_1", secret=_CLERK_SECRET):
    payload = json.dumps(ev).encode()
    return client.post("/webhooks/clerk", content=payload,
                       headers=_clerk_headers(payload, svix_id=svix_id, secret=secret))


def test_clerk_user_created_mirrors_into_db(client):
    r = _post_clerk(client, _clerk_event("user.created", "user_abc", "z@x.com"))
    assert r.status_code == 200, r.text
    # the mirrored user can now authenticate and find an existing tenant
    me = client.get("/v1/me", headers=_auth("user_abc"))
    assert me.status_code == 200
    assert me.json()["email"] == "z@x.com"


def test_clerk_bad_signature_rejected(client):
    r = _post_clerk(client, _clerk_event("user.created", "user_bad", "z@x.com"),
                    secret="whsec_" + __import__("base64").b64encode(b"wrong").decode())
    assert r.status_code == 400


def test_clerk_webhook_idempotent(client):
    ev = _clerk_event("user.created", "user_dup", "z@x.com")
    assert _post_clerk(client, ev, svix_id="msg_dup").json()["status"] == "ok"
    assert _post_clerk(client, ev, svix_id="msg_dup").json()["status"] == "duplicate"


def test_clerk_user_deleted_removes_user(client):
    _post_clerk(client, _clerk_event("user.created", "user_del", "z@x.com"), svix_id="m1")
    _post_clerk(client, _clerk_event("user.deleted", "user_del"), svix_id="m2")
    assert client.get("/v1/me", headers=_auth("user_del")).status_code == 404


# --- funding flow (core-api -> accounts-service, mocked) ----------------------

class _FakeAccounts:
    """In-memory stand-in for accounts-service used via dependency override."""
    def __init__(self):
        self.avail: dict[tuple[str, str], float] = {}

    def deposit(self, tenant, asset, amount):
        self.avail[(tenant, asset)] = self.avail.get((tenant, asset), 0.0) + float(amount)
        return self._bal(tenant, asset)

    def withdraw(self, tenant, asset, amount):
        import accounts_client
        if self.avail.get((tenant, asset), 0.0) < float(amount):
            raise accounts_client.AccountsInsufficientFunds("available < amount")
        self.avail[(tenant, asset)] -= float(amount)
        return self._bal(tenant, asset)

    def balances(self, tenant, asset):
        return self._bal(tenant, asset)

    def all_balances(self, tenant):
        return [{"asset": a, "available": v, "reserved": 0.0}
                for (t, a), v in self.avail.items() if t == tenant]

    def _bal(self, tenant, asset):
        return {"tenant_id": tenant, "asset": asset,
                "available": self.avail.get((tenant, asset), 0.0), "reserved": 0.0}


@pytest.fixture()
def accounts(client):
    import main
    fake = _FakeAccounts()
    main.app.dependency_overrides[main.get_accounts_client] = lambda: fake
    main.app.dependency_overrides[main.get_accounts_client_optional] = lambda: fake
    yield fake
    main.app.dependency_overrides.pop(main.get_accounts_client, None)
    main.app.dependency_overrides.pop(main.get_accounts_client_optional, None)


def test_deposit_then_balance(client, accounts):
    h = _auth("fnd-1", "p@x.com")
    client.post("/v1/sessions", headers=h)
    r = client.post("/v1/funding/deposit", headers=h, json={"asset": "USD", "amount": "1000"})
    assert r.status_code == 200, r.text
    assert r.json()["available"] == 1000.0
    bal = client.get("/v1/funding/balances", headers=h, params={"asset": "USD"}).json()
    assert bal["available"] == 1000.0


def test_withdraw_requires_permission_and_succeeds_for_owner(client, accounts):
    h = _auth("fnd-2", "q@x.com")
    client.post("/v1/sessions", headers=h)
    client.post("/v1/funding/deposit", headers=h, json={"asset": "USD", "amount": "500"})
    r = client.post("/v1/funding/withdraw", headers=h, json={"asset": "USD", "amount": "200"})
    assert r.status_code == 200, r.text
    assert r.json()["available"] == 300.0


def test_withdraw_insufficient_funds_is_409(client, accounts):
    h = _auth("fnd-3", "r@x.com")
    client.post("/v1/sessions", headers=h)
    client.post("/v1/funding/deposit", headers=h, json={"asset": "USD", "amount": "100"})
    r = client.post("/v1/funding/withdraw", headers=h, json={"asset": "USD", "amount": "150"})
    assert r.status_code == 409


def test_funding_audited(client, accounts):
    h = _auth("fnd-4", "s@x.com")
    client.post("/v1/sessions", headers=h)
    client.post("/v1/funding/deposit", headers=h, json={"asset": "USD", "amount": "1000"})
    actions = [e["action"] for e in client.get("/v1/audit", headers=h).json()]
    assert "funding.deposit" in actions


def test_dashboard_aggregates_profile_entitlements_balances(client, accounts):
    h = _ready_pro(client, "dsh-1")              # onboarded + pro plan
    # fund via the API so the fake records under the real tenant id
    client.post("/v1/funding/deposit", headers=h, json={"asset": "USD", "amount": "2500"})
    d = client.get("/v1/dashboard", headers=h).json()
    assert d["profile"]["onboarding_status"] == "trading_ready"
    assert d["entitlements"]["plan"] == "pro"
    assert d["entitlements"]["live_trading"] is True
    usd = [b for b in d["balances"] if b["asset"] == "USD"]
    assert usd and usd[0]["available"] == 2500.0


def test_dashboard_works_without_funding_configured(client, monkeypatch):
    # no accounts override + ACCOUNTS_SERVICE_URL unset → balances empty, still 200
    monkeypatch.delenv("ACCOUNTS_SERVICE_URL", raising=False)
    h = _auth("dsh-2", "u@x.com")
    client.post("/v1/sessions", headers=h)
    d = client.get("/v1/dashboard", headers=h)
    assert d.status_code == 200
    assert d.json()["balances"] == []


def test_funding_503_when_unconfigured(client, monkeypatch):
    # no dependency override + ACCOUNTS_SERVICE_URL unset → 503
    monkeypatch.delenv("ACCOUNTS_SERVICE_URL", raising=False)
    h = _auth("fnd-5", "t@x.com")
    client.post("/v1/sessions", headers=h)
    r = client.get("/v1/funding/balances", headers=h, params={"asset": "USD"})
    assert r.status_code == 503


def test_eligibility_snapshot_reflects_onboarding_and_plan():
    import eligibility
    from db import Market, OnboardingStatus, PlanTier, Tenant

    ready_pro = Tenant(id="t1", plan=PlanTier.PRO, market=Market.GLOBAL,
                       onboarding_status=OnboardingStatus.TRADING_READY, live_enabled=True)
    snap = eligibility.build_snapshot(ready_pro)
    assert snap["trading_ready"] is True
    assert snap["live_allowed"] is True            # pro plan
    assert set(snap["markets"]) == {"global", "africa"}
    assert snap["live_enabled"] is True

    new_free = Tenant(id="t2", plan=PlanTier.FREE,
                      onboarding_status=OnboardingStatus.ACCOUNT_CREATED, live_enabled=False)
    snap2 = eligibility.build_snapshot(new_free)
    assert snap2["trading_ready"] is False
    assert snap2["live_allowed"] is False
    assert snap2["markets"] == []


def test_audit_isolated_per_tenant(client):
    ha = _ready_pro(client, "au-3")
    _ready_pro(client, "au-4")
    # au-3 only sees its own tenant's entries
    entries = client.get("/v1/audit", headers=ha).json()
    assert entries
    details = " ".join(e["detail"] for e in entries)
    assert "au-4" not in details


def test_local_mode_rejects_non_local_bearer(client):
    # a raw token (not 'local:...') is refused when no Firebase is configured
    r = client.get("/v1/me", headers={"Authorization": "Bearer some-random-jwt"})
    assert r.status_code == 401
