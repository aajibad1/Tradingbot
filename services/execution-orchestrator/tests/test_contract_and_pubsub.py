"""execution-orchestrator: doc-06 API contract wiring + emulator-aware Pub/Sub gating."""

from __future__ import annotations

import main
from fastapi.testclient import TestClient

from shared.http import CORRELATION_ID_HEADER, REQUEST_ID_HEADER


def test_api_contract_correlation_headers_present():
    """install_contract is wired: every response echoes x-request-id / x-correlation-id."""
    r = TestClient(main.app).get("/healthz")
    assert r.status_code == 200
    assert r.headers[REQUEST_ID_HEADER].startswith("req_")
    assert r.headers[CORRELATION_ID_HEADER].startswith("corr_")


def test_start_subscribers_noop_without_pubsub(monkeypatch):
    """Local mode (no GCP_PROJECT_ID, no PUBSUB_EMULATOR_HOST) → no subscriber,
    no exception. Mirrors the rest of the wedge so the service runs in the
    no-cloud local stack."""
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("PUBSUB_EMULATOR_HOST", raising=False)
    main._subscriber_client = None
    main._start_subscribers()
    assert main._subscriber_client is None


def test_start_subscribers_uses_emulator_project(monkeypatch):
    """With PUBSUB_EMULATOR_HOST set (and GCP_PROJECT_ID unset), the orchestrator
    joins the emulator stack — it must attempt to subscribe under the emulator
    project rather than skipping. We stub the pubsub client so no network runs."""
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "local-dev")

    subscribed: list[str] = []

    class _FakeFuture:
        def cancel(self):
            pass

    class _FakeSub:
        def subscription_path(self, project, sub):
            return f"projects/{project}/subscriptions/{sub}"

        def subscribe(self, path, callback):
            subscribed.append(path)
            return _FakeFuture()

        def close(self):
            pass

    import sys
    import types

    fake_pubsub = types.SimpleNamespace(SubscriberClient=lambda: _FakeSub())
    fake_mod = types.ModuleType("google.cloud.pubsub_v1")
    monkeypatch.setitem(sys.modules, "google.cloud.pubsub_v1", fake_pubsub)

    main._subscriber_client = None
    main._futures.clear()
    main._start_subscribers()

    assert subscribed and "projects/local-dev/subscriptions/arb-approved-orchestrator" in subscribed[0]
    main._stop_subscribers()
