"""Tests for secret redaction in log strings."""

from shared.utils.log_redact import redact_secrets


def test_redacts_cryptopanic_auth_token_in_url() -> None:
    err = (
        "Client error '404 Not Found' for url "
        "'https://cryptopanic.com/api/v1/posts/?auth_token=secret123&filter=hot'"
    )
    out = redact_secrets(err)
    assert "secret123" not in out
    assert "auth_token=***" in out
    assert "filter=hot" in out  # non-secret params preserved


def test_redacts_various_query_secrets() -> None:
    out = redact_secrets("?api_key=AKIA123&token=tok_456&apikey=zzz&password=p@ss")
    for leaked in ("AKIA123", "tok_456", "zzz", "p@ss"):
        assert leaked not in out
    assert out.count("=***") == 4


def test_redacts_bearer_token() -> None:
    assert redact_secrets("Authorization: Bearer abc.def.ghi") == "Authorization: Bearer ***"


def test_passthrough_when_no_secret() -> None:
    assert redact_secrets("plain message, no secrets") == "plain message, no secrets"
    assert redact_secrets("") == ""
