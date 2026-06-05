"""Country-based channel routing (NOTIF-07 / TDD §6.1).

Returns the ordered list of channels to try for a user. The dispatcher walks
this order and falls back to the next channel on failure.
"""

from __future__ import annotations

from models import Channel, Priority

# WhatsApp-primary African markets.
_AFRICA_WHATSAPP = {"NG", "KE", "GH", "TZ", "UG", "RW"}


def channels_for_country(country: str, priority: Priority = Priority.NORMAL) -> list[Channel]:
    c = (country or "").upper()
    if c in _AFRICA_WHATSAPP:
        order = [Channel.WHATSAPP, Channel.SMS, Channel.PUSH]
    elif c == "ZA":
        order = [Channel.WHATSAPP, Channel.TELEGRAM, Channel.PUSH, Channel.SMS]
    else:  # global default — crypto-savvy users skew Telegram
        order = [Channel.TELEGRAM, Channel.PUSH, Channel.EMAIL]

    # Critical alerts (kill switch, loss limit) MUST reach an SMS (NOTIF-03) —
    # append it as a guaranteed fallback if routing didn't already include it.
    if priority is Priority.CRITICAL and Channel.SMS not in order:
        order = [*order, Channel.SMS]
    return order
