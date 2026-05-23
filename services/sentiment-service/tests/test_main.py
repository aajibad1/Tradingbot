"""Tests for the FastAPI endpoints."""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient


@patch.dict(os.environ, {"GCP_PROJECT_ID": "", "REDIS_URL": ""}, clear=True)
def test_healthz_returns_ok() -> None:
    from main import app

    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@patch.dict(os.environ, {"GCP_PROJECT_ID": "", "REDIS_URL": ""}, clear=True)
def test_refresh_with_no_keys_returns_503() -> None:
    from main import app

    with TestClient(app) as client:
        resp = client.post("/sentiment/refresh")
        assert resp.status_code == 503
        assert "no sentiment sources" in resp.json()["detail"].lower()


@patch.dict(
    os.environ,
    {
        "GCP_PROJECT_ID": "",
        "REDIS_URL": "",
        "LOCAL_SECRET_PERPLEXITY_API_KEY": "pk",
        "LOCAL_SECRET_CRYPTOPANIC_API_KEY": "ck",
    },
    clear=True,
)
def test_status_reports_configured_sources() -> None:
    from main import app

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["sources_configured"]["fear_greed"] is True
        assert body["sources_configured"]["perplexity"] is True
        assert body["sources_configured"]["cryptopanic"] is True


@patch.dict(os.environ, {"GCP_PROJECT_ID": "", "REDIS_URL": ""}, clear=True)
def test_status_with_no_keys_shows_only_fg_configured() -> None:
    from main import app

    with TestClient(app) as client:
        resp = client.get("/status")
        body = resp.json()
        # F&G needs no key; the other two should be marked unavailable.
        assert body["sources_configured"]["fear_greed"] is True
        assert body["sources_configured"]["perplexity"] is False
        assert body["sources_configured"]["cryptopanic"] is False


@patch.dict(os.environ, {"GCP_PROJECT_ID": "", "REDIS_URL": ""}, clear=True)
def test_current_endpoint_requires_redis_url() -> None:
    from main import app

    with TestClient(app) as client:
        resp = client.get("/sentiment/current")
        assert resp.status_code == 503
