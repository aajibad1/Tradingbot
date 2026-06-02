"""Shared test fixtures for sentiment-service.

The secret loader memoises results per process (``lru_cache``). Without
clearing it between tests, a ``None`` cached while a key was unset (or a
``test-token`` cached while it was set) leaks into later tests — producing
order-dependent failures and even real network calls with a stale token.
This autouse fixture clears the cache before every test so each test sees the
environment it sets up, nothing more.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_secret_cache():
    from secret_loader import get_secret

    get_secret.cache_clear()
    yield
    get_secret.cache_clear()
