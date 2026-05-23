import hashlib
import hmac
import time

from approval_gate import verify_slack_signature


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_valid_signature_accepted() -> None:
    secret = "shh"
    ts = str(int(time.time()))
    body = b"payload={}"
    sig = _sign(secret, ts, body)
    assert verify_slack_signature(secret, ts, body, sig)


def test_tampered_body_rejected() -> None:
    secret = "shh"
    ts = str(int(time.time()))
    sig = _sign(secret, ts, b"payload={}")
    assert not verify_slack_signature(secret, ts, b"payload={evil}", sig)


def test_old_timestamp_rejected() -> None:
    secret = "shh"
    ts = str(int(time.time()) - 3600)  # 1 hour old
    sig = _sign(secret, ts, b"payload={}")
    assert not verify_slack_signature(secret, ts, b"payload={}", sig)


def test_bad_timestamp_format_rejected() -> None:
    assert not verify_slack_signature("shh", "not-a-number", b"x", "v0=abc")
