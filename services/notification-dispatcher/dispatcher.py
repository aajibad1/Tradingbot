"""Routing + fallback dispatch (NOTIF-07 + channel fallback / FAULT-04).

Walks the country-routed channel order; the first channel that has BOTH a
recipient and a configured provider AND succeeds wins. Otherwise it falls
through to the next channel and finally reports non-delivery.
"""

from __future__ import annotations

import logging

from models import Attempt, Channel, DispatchResult, Notification
from routing import channels_for_country

from shared.utils.log_redact import redact_secrets

logger = logging.getLogger("notification-dispatcher")


def dispatch(notif: Notification, providers: dict[Channel, object]) -> DispatchResult:
    order = channels_for_country(notif.country, notif.priority)
    attempts: list[Attempt] = []

    for channel in order:
        recipient = notif.recipients.get(channel)
        provider = providers.get(channel)
        if recipient is None or provider is None:
            continue  # channel not usable for this user (no address or not configured)
        try:
            if provider.send(recipient, notif.message):  # type: ignore[attr-defined]
                attempts.append(Attempt(channel=channel, ok=True))
                return DispatchResult(delivered=True, channel=channel, attempts=attempts)
            attempts.append(Attempt(channel=channel, ok=False, error="provider returned false"))
        except Exception as e:  # noqa: BLE001 — try the next channel
            attempts.append(Attempt(channel=channel, ok=False, error=redact_secrets(str(e))))
            logger.warning("notify channel %s failed for user=%s: %s",
                           channel.value, notif.user_id, redact_secrets(str(e)))

    logger.error("notification undelivered user=%s tried=%s",
                 notif.user_id, [a.channel.value for a in attempts])
    return DispatchResult(delivered=False, attempts=attempts)
