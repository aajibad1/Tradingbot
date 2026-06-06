"""notification-dispatcher — routes user alerts across channels with fallback.

Implements FRD §6 / TDD §6: country-based channel routing (WhatsApp-first for
Africa, Telegram for global), with fallback to the next channel on failure and
a guaranteed SMS for critical alerts (kill switch / loss limit).

Endpoints:
  GET  /healthz
  GET  /status              configured channels
  POST /notify              dispatch one notification (returns DispatchResult)

Runs locally with no providers configured — /notify then reports the channels
it would have tried (delivered=false) rather than failing.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

import dispatcher as dispatcher_mod
from corridor import corridor_notification
from models import Channel, DispatchResult, Notification
from providers import TelegramProvider, TwilioProvider
from secret_loader import get_secret

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("notification-dispatcher")

_subscriber_client = None
_subscriber_futures: list = []


def _ops_recipients() -> dict[Channel, str]:
    """Where corridor (ops) alerts go — a Telegram chat by default, configured
    out-of-band. Empty when unset (dispatch then reports undelivered)."""
    chat = os.environ.get("OPS_TELEGRAM_CHAT")
    return {Channel.TELEGRAM: chat} if chat else {}


def _on_corridor_alert(message) -> None:
    """Turn a corridor-engine alert into an ops notification and dispatch it."""
    try:
        alert = json.loads(message.data)
    except Exception:
        logger.exception("failed to parse corridor alert")
        message.ack()
        return
    note = corridor_notification(alert, _ops_recipients())
    dispatcher_mod.dispatch(note, _build_providers())
    message.ack()


def _start_subscriber() -> None:
    global _subscriber_client
    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        logger.warning("GCP_PROJECT_ID unset — notification-dispatcher running without subscriber")
        return
    from google.cloud import pubsub_v1

    _subscriber_client = pubsub_v1.SubscriberClient()
    sub = os.environ.get("CORRIDOR_ALERTS_SUBSCRIPTION", "arb-corridor-alerts-dispatcher")
    path = _subscriber_client.subscription_path(project_id, sub)
    _subscriber_futures.append(_subscriber_client.subscribe(path, callback=_on_corridor_alert))
    logger.info("subscribed: %s", path)


def _stop_subscriber() -> None:
    for f in _subscriber_futures:
        f.cancel()
    if _subscriber_client is not None:
        _subscriber_client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_subscriber()
    try:
        yield
    finally:
        _stop_subscriber()


app = FastAPI(title="notification-dispatcher", version="0.1.0", lifespan=lifespan)


def _build_providers() -> dict[Channel, object]:
    """Construct the providers whose credentials are configured. Others are
    simply absent (routing skips them)."""
    providers: dict[Channel, object] = {}

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or get_secret("telegram-bot-token")
    if tg_token:
        providers[Channel.TELEGRAM] = TelegramProvider(tg_token)

    sid = os.environ.get("TWILIO_ACCOUNT_SID") or get_secret("twilio-account-sid")
    token = os.environ.get("TWILIO_AUTH_TOKEN") or get_secret("twilio-auth-token")
    if sid and token:
        wa_from = os.environ.get("TWILIO_WHATSAPP_FROM")
        sms_from = os.environ.get("TWILIO_SMS_FROM")
        if wa_from:
            providers[Channel.WHATSAPP] = TwilioProvider(Channel.WHATSAPP, sid, token, wa_from)
        if sms_from:
            providers[Channel.SMS] = TwilioProvider(Channel.SMS, sid, token, sms_from)
    return providers


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict[str, object]:
    return {"configured_channels": [c.value for c in _build_providers()]}


@app.post("/notify", response_model=DispatchResult)
def notify(notification: Notification) -> DispatchResult:
    return dispatcher_mod.dispatch(notification, _build_providers())
