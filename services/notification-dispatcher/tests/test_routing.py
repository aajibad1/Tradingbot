"""Country-based channel routing (NOTIF-07)."""

from models import Channel, Priority
from routing import channels_for_country


def test_africa_whatsapp_first() -> None:
    assert channels_for_country("NG")[0] is Channel.WHATSAPP
    assert channels_for_country("KE") == [Channel.WHATSAPP, Channel.SMS, Channel.PUSH]


def test_south_africa_whatsapp_then_telegram() -> None:
    assert channels_for_country("ZA") == [Channel.WHATSAPP, Channel.TELEGRAM, Channel.PUSH, Channel.SMS]


def test_global_default_telegram_first() -> None:
    assert channels_for_country("US") == [Channel.TELEGRAM, Channel.PUSH, Channel.EMAIL]
    assert channels_for_country("") == [Channel.TELEGRAM, Channel.PUSH, Channel.EMAIL]


def test_critical_guarantees_sms_fallback() -> None:
    # global routing has no SMS by default; critical appends it.
    assert Channel.SMS not in channels_for_country("US", Priority.NORMAL)
    assert channels_for_country("US", Priority.CRITICAL)[-1] is Channel.SMS
    # SA already includes SMS — not duplicated.
    assert channels_for_country("ZA", Priority.CRITICAL).count(Channel.SMS) == 1
