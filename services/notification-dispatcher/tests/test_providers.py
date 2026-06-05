"""Provider send paths (offline via httpx.MockTransport)."""

import httpx
import pytest

from models import Channel
from providers import TelegramProvider, TwilioProvider


def test_telegram_send_ok() -> None:
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return httpx.Response(200, json={"ok": True})

    p = TelegramProvider("BOT123", transport=httpx.MockTransport(handler))
    assert p.send("chat-1", "hello") is True
    assert "BOT123/sendMessage" in captured["url"]


def test_telegram_requires_token() -> None:
    with pytest.raises(RuntimeError):
        TelegramProvider("")


def test_telegram_raises_on_http_error() -> None:
    p = TelegramProvider("B", transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    with pytest.raises(httpx.HTTPStatusError):
        p.send("c", "m")


def test_twilio_whatsapp_prefixes_recipient() -> None:
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content.decode()
        return httpx.Response(201, json={"sid": "SM1"})

    p = TwilioProvider(Channel.WHATSAPP, "AC1", "tok", "+15550000",
                       transport=httpx.MockTransport(handler))
    assert p.send("+27123", "hi") is True
    assert "whatsapp%3A%2B27123" in captured["body"] or "whatsapp:+27123" in captured["body"]


def test_twilio_requires_complete_config() -> None:
    with pytest.raises(RuntimeError):
        TwilioProvider(Channel.SMS, "AC1", "", "+1")
