"""Fallback dispatch behaviour."""

from dispatcher import dispatch
from models import Channel, Notification, Priority


class _FakeProvider:
    def __init__(self, ok: bool = True, raises: str | None = None):
        self.ok, self.raises, self.sent = ok, raises, []

    def send(self, recipient: str, message: str) -> bool:
        if self.raises:
            raise RuntimeError(self.raises)
        self.sent.append((recipient, message))
        return self.ok


def _notif(country: str, recipients: dict, priority=Priority.NORMAL) -> Notification:
    return Notification(user_id="u1", country=country, message="hi",
                        priority=priority, recipients=recipients)


def test_delivers_on_first_available_channel() -> None:
    tg = _FakeProvider(ok=True)
    res = dispatch(_notif("US", {Channel.TELEGRAM: "chat1"}), {Channel.TELEGRAM: tg})
    assert res.delivered and res.channel is Channel.TELEGRAM
    assert tg.sent == [("chat1", "hi")]


def test_falls_back_to_next_channel_on_failure() -> None:
    # US order: telegram → push → email. Telegram raises, email succeeds.
    providers = {Channel.TELEGRAM: _FakeProvider(raises="down"), Channel.EMAIL: _FakeProvider(ok=True)}
    res = dispatch(_notif("US", {Channel.TELEGRAM: "c", Channel.EMAIL: "e@x.com"}), providers)
    assert res.delivered and res.channel is Channel.EMAIL
    assert [a.channel for a in res.attempts] == [Channel.TELEGRAM, Channel.EMAIL]


def test_skips_channels_without_recipient_or_provider() -> None:
    # Provider exists for telegram but the user has no telegram recipient → skip;
    # email has a recipient and provider → used.
    providers = {Channel.TELEGRAM: _FakeProvider(), Channel.EMAIL: _FakeProvider()}
    res = dispatch(_notif("US", {Channel.EMAIL: "e@x.com"}), providers)
    assert res.delivered and res.channel is Channel.EMAIL


def test_undelivered_when_all_fail() -> None:
    providers = {Channel.TELEGRAM: _FakeProvider(raises="x")}
    res = dispatch(_notif("US", {Channel.TELEGRAM: "c"}), providers)
    assert res.delivered is False and res.channel is None


def test_critical_reaches_sms_fallback() -> None:
    # Global critical: telegram fails, the appended SMS succeeds.
    providers = {Channel.TELEGRAM: _FakeProvider(raises="x"), Channel.SMS: _FakeProvider(ok=True)}
    res = dispatch(_notif("US", {Channel.TELEGRAM: "c", Channel.SMS: "+123"}, Priority.CRITICAL), providers)
    assert res.delivered and res.channel is Channel.SMS


def test_error_is_redacted_in_attempts() -> None:
    providers = {Channel.TELEGRAM: _FakeProvider(raises="auth failed token=supersecret")}
    res = dispatch(_notif("US", {Channel.TELEGRAM: "c"}), providers)
    err = res.attempts[0].error or ""
    assert "supersecret" not in err and "token=***" in err
