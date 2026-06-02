"""Tests for the consolidated secret loader — both policy modes.

The GCP fetch path is not exercised here (the CI shared gate installs no
google-cloud libs); these cover the local-env fallback and the divergent
required/optional behaviour that the per-service wrappers depend on.
"""

import pytest

from shared.utils.secret_loader import get_secret


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
