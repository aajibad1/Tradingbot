"""Skeleton tests — assert the service stands up and the business endpoints are
explicitly 501 (Phase-4 pickup). Replace with real tests when implemented."""

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_healthz() -> None:
    assert client.get("/healthz").json()["status"] == "ok"


def test_session_not_implemented() -> None:
    r = client.post("/link/session", json={"user_id": "u1", "exchange": "binanceus"})
    assert r.status_code == 501


def test_callback_not_implemented() -> None:
    assert client.post("/link/callback", json={}).status_code == 501


def test_status_not_implemented() -> None:
    assert client.get("/link/status", params={"user_id": "u1", "exchange": "kraken"}).status_code == 501


def test_revoke_not_implemented() -> None:
    assert client.delete("/link/u1/kraken").status_code == 501
