"""agent-evals: scoring + run/verdict endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import evals
import main


# --- pure scoring ----------------------------------------------------------- #


def test_passing_metrics():
    passed, failures = evals.evaluate({"accuracy": 0.9, "hallucination_rate": 0.02, "latency_ms": 1200})
    assert passed and failures == []


def test_each_threshold_can_fail():
    p, f = evals.evaluate({"accuracy": 0.5, "hallucination_rate": 0.02, "latency_ms": 100})
    assert not p and any("accuracy" in x for x in f)
    p, f = evals.evaluate({"accuracy": 0.9, "hallucination_rate": 0.2, "latency_ms": 100})
    assert not p and any("hallucination" in x for x in f)
    p, f = evals.evaluate({"accuracy": 0.9, "hallucination_rate": 0.02, "latency_ms": 99999})
    assert not p and any("latency" in x for x in f)


def test_missing_metric_is_a_failure():
    passed, failures = evals.evaluate({"accuracy": 0.9})
    assert not passed
    assert any("hallucination_rate not measured" in x for x in failures)
    assert any("latency_ms not measured" in x for x in failures)


# --- endpoints -------------------------------------------------------------- #


@pytest.fixture()
def client():
    main._runs.clear()
    return TestClient(main.app)


def test_run_and_verdict(client):
    client.post("/v1/evals/run", json={
        "agent": "ranker", "prompt_version": "v1",
        "metrics": {"accuracy": 0.91, "hallucination_rate": 0.01, "latency_ms": 800},
    })
    v = client.get("/v1/evals/verdict", params={"agent": "ranker", "version": "v1"}).json()
    assert v["passed"] is True


def test_verdict_uses_latest_run(client):
    body = {"agent": "ranker", "prompt_version": "v1"}
    client.post("/v1/evals/run", json={**body, "metrics": {"accuracy": 0.5, "hallucination_rate": 0.01, "latency_ms": 800}})
    client.post("/v1/evals/run", json={**body, "metrics": {"accuracy": 0.95, "hallucination_rate": 0.01, "latency_ms": 800}})
    assert client.get("/v1/evals/verdict", params={"agent": "ranker", "version": "v1"}).json()["passed"] is True


def test_verdict_404_when_never_evaluated(client):
    r = client.get("/v1/evals/verdict", params={"agent": "ghost", "version": "v9"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_evaluated"


def test_thresholds_exposed(client):
    assert client.get("/v1/evals/thresholds").json()["thresholds"]["min_accuracy"] == 0.80
