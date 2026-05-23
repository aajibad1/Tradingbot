"""dashboard-api smoke tests.

Heavy I/O paths (BigQuery, Redis) are stubbed out — the goal here is to
prove the aggregator's wiring + JSON shape contract with the dashboard
frontend, not to integration-test GCP.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def _import_app():
    """Lazy import so env-var patches land before module load."""
    import importlib
    import main as main_mod

    importlib.reload(main_mod)
    return main_mod


def test_healthz_returns_ok() -> None:
    with patch.dict(os.environ, {"GCP_PROJECT_ID": "test-proj"}, clear=False):
        main_mod = _import_app()
        with TestClient(main_mod.app) as client:
            resp = client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}


def test_summary_returns_503_when_redis_down() -> None:
    with patch.dict(os.environ, {"GCP_PROJECT_ID": "test-proj"}, clear=False):
        main_mod = _import_app()

        def _broken_redis():
            r = MagicMock()
            r.ping.side_effect = ConnectionError("redis down")
            return r

        with patch.object(main_mod, "_redis", _broken_redis):
            with TestClient(main_mod.app) as client:
                resp = client.get("/api/summary")
                assert resp.status_code == 503
                assert resp.json()["detail"] == "redis_unavailable"


def test_summary_happy_path_shape() -> None:
    """Even with empty BQ + Redis (all queries fail/return nothing), the
    aggregator returns a valid DashboardSummary with the expected keys."""
    with patch.dict(os.environ, {"GCP_PROJECT_ID": "test-proj"}, clear=False):
        main_mod = _import_app()

        fake_redis = MagicMock()
        fake_redis.ping.return_value = True
        fake_redis.get.return_value = None
        fake_redis.scan_iter.return_value = iter([])

        # All BQ queries throw; the aggregator should swallow and return defaults.
        broken_bq = MagicMock()
        broken_bq.query.side_effect = RuntimeError("BQ unreachable")

        with patch.object(main_mod, "_redis", lambda: fake_redis), \
             patch.object(main_mod, "_bq", lambda: broken_bq):
            with TestClient(main_mod.app) as client:
                resp = client.get("/api/summary")
                assert resp.status_code == 200
                body = resp.json()

                # Top-level keys the frontend depends on
                for key in ("mode", "kill_switch_active", "kpis", "risk_bars",
                            "opportunities", "audit_log", "pnl_history"):
                    assert key in body, f"missing top-level key: {key}"

                # KPIs are zero-valued but present
                assert body["kpis"]["capital_usd"] == 0.0
                assert body["kpis"]["live_opportunities"] == 0
                assert body["kpis"]["open_positions"] == 0

                # No data → empty arrays, never null
                assert body["opportunities"] == []
                assert body["audit_log"] == []
                assert body["pnl_history"] == []


def test_summary_reflects_kill_switch_state() -> None:
    with patch.dict(os.environ, {"GCP_PROJECT_ID": "test-proj"}, clear=False):
        main_mod = _import_app()

        fake_redis = MagicMock()
        fake_redis.ping.return_value = True
        # kill switch active + capital set
        fake_redis.get.side_effect = lambda key: {
            "risk:kill_switch:active": "1",
            "risk:capital_usd": "10000",
        }.get(key)
        fake_redis.scan_iter.return_value = iter([])

        broken_bq = MagicMock()
        broken_bq.query.side_effect = RuntimeError("bq down")

        with patch.object(main_mod, "_redis", lambda: fake_redis), \
             patch.object(main_mod, "_bq", lambda: broken_bq):
            with TestClient(main_mod.app) as client:
                body = client.get("/api/summary").json()
                assert body["kill_switch_active"] is True
                assert body["mode"] == "halted"
                assert body["kpis"]["capital_usd"] == 10000.0
