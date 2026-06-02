"""Shared pytest fixtures.

We deliberately avoid pulling fakeredis in as a test dep. The risk-engine only
uses a small slice of redis-py (``get``, ``set``, ``delete``, ``scan_iter``,
``pipeline``, plus ``hset``/``hgetall`` for the liquidation-monitor's
``risk:positions:open`` hash), so we ship a tiny in-process fake here and
have every test monkeypatch :func:`state.get_redis` to return it.

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

    def incrbyfloat(self, key: str, amount) -> "_FakePipeline":
        self._ops.append(("incrbyfloat", (key, amount)))
        return self

    def incr(self, key: str) -> "_FakePipeline":
        self._ops.append(("incr", (key,)))
        return self

    def decr(self, key: str) -> "_FakePipeline":
        self._ops.append(("decr", (key,)))
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
        self._hashes: dict[str, dict[str, str]] = {}

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
            if k in self._hashes:
                del self._hashes[k]
                n += 1
        return n

    def scan_iter(self, match: str = "*"):
        # Only the "prefix*" pattern is used inside state.py.
        if match.endswith("*"):
            prefix = match[:-1]
            return iter([k for k in list(self._kv) if k.startswith(prefix)])
        return iter([k for k in list(self._kv) if k == match])

    def incrbyfloat(self, key: str, amount) -> float:
        val = float(self._kv.get(key, 0.0)) + float(amount)
        self._kv[key] = repr(val)
        return val

    def incr(self, key: str) -> int:
        val = int(float(self._kv.get(key, 0))) + 1
        self._kv[key] = str(val)
        return val

    def decr(self, key: str) -> int:
        val = int(float(self._kv.get(key, 0))) - 1
        self._kv[key] = str(val)
        return val

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    # -- hash ops --------------------------------------------------------- #
    def hset(self, key: str, field: str, value) -> int:
        h = self._hashes.setdefault(key, {})
        existed = field in h
        h[field] = str(value)
        return 0 if existed else 1

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    def hget(self, key: str, field: str) -> str | None:
        return self._hashes.get(key, {}).get(field)

    def hdel(self, key: str, *fields: str) -> int:
        h = self._hashes.get(key)
        if h is None:
            return 0
        n = 0
        for f in fields:
            if f in h:
                del h[f]
                n += 1
        return n

    # -- helpers for tests ----------------------------------------------- #
    def flushall(self) -> None:
        self._kv.clear()
        self._hashes.clear()


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
