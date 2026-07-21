from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def _signal_payload(signal_id="sig-http-1", **overrides):
    payload = dict(
        signal_id=signal_id, symbol="BTC/USD:PERP", family="momentum_dislocation",
        direction="long", gross_edge_bps=30.0, confidence=0.8, expiry_ms=2000,
        regime="trending", detected_at=datetime(2026, 1, 1, 12, 0, 0).isoformat(),
    )
    payload.update(overrides)
    return payload


def _leg_payload(size=1.0, fill_price=60_000.0):
    return dict(exchange="hyperliquid", side="buy", asset="BTC", size=size,
                fill_price=fill_price, fee_usd=1.0, slippage_usd=1.0,
                filled_at=datetime(2026, 1, 1, 13, 0, 0).isoformat())


def _fill_payload(opportunity_id="sig-http-1", net_pnl_usd=120.0):
    return dict(id="trade-http-1", opportunity_id=opportunity_id, type="paper",
                legs=[_leg_payload(), _leg_payload()], gross_pnl_usd=150.0,
                net_pnl_usd=net_pnl_usd, status="closed",
                opened_at=datetime(2026, 1, 1, 12, 30, 0).isoformat(),
                closed_at=datetime(2026, 1, 1, 13, 0, 0).isoformat(), directional=True)


def setup_function() -> None:
    # Ledger is module-level state — reset between tests.
    main._ledger.__init__()  # noqa: SLF001 — test-only reset of private state


def test_healthz() -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_ingest_signal_then_fill_scores_and_reports() -> None:
    r = client.post("/ingest-signal", json=_signal_payload())
    assert r.status_code == 200
    assert r.json()["pending_count"] == 1

    r = client.post("/ingest-fill", json=_fill_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["scored"] is True
    assert body["label"] == "true_positive"

    report = client.get("/report").json()
    assert report["n"] == 1
    assert report["false_positive_rate"] == 0.0

    assert client.get("/pending").json()["pending_count"] == 0


def test_ingest_fill_without_matching_signal_is_a_no_op() -> None:
    r = client.post("/ingest-fill", json=_fill_payload(opportunity_id="never-journaled"))
    assert r.json() == {"scored": False}
    assert client.get("/report").json()["n"] == 0


def test_sweep_expired_via_http() -> None:
    client.post("/ingest-signal", json=_signal_payload(signal_id="sig-decay", expiry_ms=1))
    r = client.post("/sweep-expired")
    body = r.json()
    assert body["swept"] == 1
    assert body["signal_ids"] == ["sig-decay"]
    assert client.get("/report").json()["decay_rate"] == 1.0
