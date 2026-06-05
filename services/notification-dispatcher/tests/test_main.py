"""Endpoint tests (providers stubbed)."""

import pytest
from fastapi.testclient import TestClient

import main
from models import Channel


class _FakeProvider:
    def __init__(self, ok=True):
        self.ok = ok

    def send(self, recipient, message):
        return self.ok


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "_build_providers",
                        lambda: {Channel.TELEGRAM: _FakeProvider(True)})
    return TestClient(main.app)


def test_healthz(client) -> None:
    assert client.get("/healthz").json()["status"] == "ok"


def test_status_lists_configured_channels(client) -> None:
    assert client.get("/status").json()["configured_channels"] == ["telegram"]


def test_notify_delivers(client) -> None:
    body = client.post("/notify", json={
        "user_id": "u1", "country": "US", "message": "you earned $1.22",
        "recipients": {"telegram": "chat-1"},
    }).json()
    assert body["delivered"] is True
    assert body["channel"] == "telegram"


def test_notify_undelivered_when_channel_not_configured(client) -> None:
    # User only has a WhatsApp recipient but only Telegram is configured.
    body = client.post("/notify", json={
        "user_id": "u1", "country": "NG", "message": "hi",
        "recipients": {"whatsapp": "+234123"},
    }).json()
    assert body["delivered"] is False
