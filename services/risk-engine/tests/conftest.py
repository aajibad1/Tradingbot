"""Shared pytest fixtures.

We deliberately avoid pulling fakeredis in as a test dep. The risk-engine only
uses a small slice of redis-py (``get``, ``set``, ``delete``, ``scan_iter``,
``pipeline``), so we ship a tiny in-process fake here and have every test
monkeypatch :func:`state.get_redis` to return it.

This keeps the test image identical to the production image (no extra wheels)
and means the unit tests run on a vanilla Python install.
"""

from __future__ import annotations

import importlib
import sys
from typing import Iterator

import pytest


class _FakePipeline:
    def __init__(self, parent: "FakeRedis") -> None:
        self._parent = parent
        self._ops: list[tuple[str, tuple]] = []

    def set(self, key: str, value) -> "_FakePipeline":
        self._ops.append(("set", (key, value)))
        return self

    def delete(self, *keys: str) -> "_FakePipeline":
        self._ops.append(("delete", keys))
        return self

    def execute(self) -> list:
        results = []
        for op, args in self._ops:
            results.append(getattr(self._parent, op)(*args))
        self._ops.clear()
        return results


class FakeRedis:
    """Minimal in-memory stand-in for the redis-py surface we use."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}

    # -- core ops --------------------------------------------------------- #
    def get(self, key: str) -> str | None:
        return self._kv.get(key)

    def set(self, key: str, value) -> bool:
        self._kv[key] = str(value)
        return True

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                n += 1
        return n

    def scan_iter(self, match: str = "*"):
        # Only the "prefix*" pattern is used inside state.py.
        if match.endswith("*"):
            prefix = match[:-1]
            return iter([k for k in list(self._kv) if k.startswith(prefix)])
        return iter([k for k in list(self._kv) if k == match])

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    # -- helpers for tests ----------------------------------------------- #
    def flushall(self) -> None:
        self._kv.clear()


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRedis]:
    """A FakeRedis bound into every callsite that resolves ``get_redis``.

    Both ``state.get_redis`` (the canonical export) and ``main.get_redis`` (the
    name ``main.py`` bound at import time via ``from state import get_redis``)
    are patched, because Python's ``from X import Y`` creates a new binding
    that won't see a later monkeypatch on ``X.Y``.

    The fixture also seeds ``risk:capital_usd = 100_000`` so callers don't
    have to remember the bootstrap step. Tests that need an unset capital can
    ``fake_redis.delete("risk:capital_usd")``.
    """
    fake = FakeRedis()
    fake.set("risk:capital_usd", "100000")

    # Make sure environment is deterministic BEFORE importing main, so the
    # FastAPI app is created against a clean env.
    monkeypatch.setenv("KILL_SWITCH_RESET_TOKEN", "test-token")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_ALERT_WEBHOOK", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)

    # Force a re-import of main on every test so the `from state import get_redis`
    # binding lands fresh — then patch BOTH name resolutions.
    for mod_name in ("main", "state"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import state as state_module  # noqa: PLC0415
    import main as main_module  # noqa: PLC0415

    monkeypatch.setattr(state_module, "get_redis", lambda: fake)
    monkeypatch.setattr(main_module, "get_redis", lambda: fake)

    yield fake


@pytest.fixture()
def app(fake_redis):
    """Hand back the FastAPI app bound to the fake redis."""
    return importlib.import_module("main").app
