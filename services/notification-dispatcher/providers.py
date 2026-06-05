"""Channel providers. Each exposes ``send(recipient, message) -> bool``.

Telegram (free) and Twilio (WhatsApp + SMS) are implemented; Push/Email/Slack
are left unconfigured (no provider → routing skips them) until added. HTTP is
via httpx with an injectable transport so the send path is testable offline.
"""

from __future__ import annotations

import logging

import httpx

from models import Channel

logger = logging.getLogger("notification-dispatcher")


class Provider:
    channel: Channel

    def send(self, recipient: str, message: str) -> bool:
        raise NotImplementedError


class TelegramProvider(Provider):
    channel = Channel.TELEGRAM

    def __init__(self, bot_token: str, timeout_s: float = 5.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        if not bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN unset")
        self._token = bot_token
        self._timeout_s = timeout_s
        self._transport = transport

    def send(self, recipient: str, message: str) -> bool:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
            resp = client.post(url, json={"chat_id": recipient, "text": message})
            resp.raise_for_status()
            return bool(resp.json().get("ok", False))


class TwilioProvider(Provider):
    """WhatsApp or SMS via Twilio's Messages API (channel set at construction)."""

    def __init__(self, channel: Channel, account_sid: str, auth_token: str, from_addr: str,
                 timeout_s: float = 5.0, transport: httpx.BaseTransport | None = None) -> None:
        if not (account_sid and auth_token and from_addr):
            raise RuntimeError("Twilio credentials/from address incomplete")
        self.channel = channel
        self._sid = account_sid
        self._token = auth_token
        self._from = from_addr
        self._timeout_s = timeout_s
        self._transport = transport

    def send(self, recipient: str, message: str) -> bool:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        # WhatsApp recipients/senders are prefixed "whatsapp:".
        prefix = "whatsapp:" if self.channel is Channel.WHATSAPP else ""
        data = {"To": f"{prefix}{recipient}", "From": f"{prefix}{self._from}", "Body": message}
        with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
            resp = client.post(url, data=data, auth=(self._sid, self._token))
            resp.raise_for_status()
            return True
