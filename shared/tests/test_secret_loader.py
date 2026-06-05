"""Tests for the consolidated secret loader — both policy modes.

The GCP fetch path is not exercised here (the CI shared gate installs no
google-cloud libs); these cover the local-env fallback and the divergent
required/optional behaviour that the per-service wrappers depend on.
"""

import pytest

from shared.utils.secret_loader import get_exchange_credentials, get_secret


def test_local_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_SECRET_ARB_KRAKEN_KEY", "local-value")
    # required flag is irrelevant when a local override exists.
    assert get_secret("arb-kraken-key", required=True) == "local-value"
    assert get_secret("arb-kraken-key", required=False) == "local-value"


def test_required_raises_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_SECRET_ARB_MISSING_KEY", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(RuntimeError):
        get_secret("arb-missing-key", required=True)


def test_optional_returns_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_SECRET_ARB_MISSING_KEY", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    assert get_secret("arb-missing-key", required=False) is None
    assert get_secret("arb-missing-key") is None  # default is optional


def test_default_tenant_uses_platform_exchange_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_SECRET_ARB_KRAKEN_KEY", "platform-key")
    monkeypatch.setenv("LOCAL_SECRET_ARB_KRAKEN_SECRET", "platform-secret")
    creds = get_exchange_credentials("kraken")  # default tenant
    assert creds["apiKey"] == "platform-key"
    assert creds["secret"] == "platform-secret"


def test_user_tenant_uses_own_exchange_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # A user's creds come from user-{uid}-{ex}-* — never the platform's.
    monkeypatch.setenv("LOCAL_SECRET_USER_U42_KRAKEN_KEY", "u42-key")
    monkeypatch.setenv("LOCAL_SECRET_USER_U42_KRAKEN_SECRET", "u42-secret")
    monkeypatch.setenv("LOCAL_SECRET_ARB_KRAKEN_KEY", "platform-key")  # must NOT be used
    creds = get_exchange_credentials("kraken", "u42")
    assert creds["apiKey"] == "u42-key"
    assert creds["secret"] == "u42-secret"


def test_user_tenant_without_keys_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # No cross-tenant fallback: a user who hasn't connected an exchange errors.
    for k in ("LOCAL_SECRET_USER_U99_BINANCEUS_KEY", "LOCAL_SECRET_USER_U99_BINANCEUS_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.setenv("LOCAL_SECRET_ARB_BINANCEUS_KEY", "platform-key")  # present but must NOT leak
    with pytest.raises(RuntimeError):
        get_exchange_credentials("binance.us", "u99")
