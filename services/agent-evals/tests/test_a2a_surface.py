"""agent-evals A2A surface — report a version's pass/fail verdict."""
from __future__ import annotations

import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def _verdict(data, req_id="1"):
    return client.post("/a2a", json={
        "jsonrpc": "2.0", "id": req_id, "method": "message/send",
        "params": {"message": {"kind": "message", "role": "user",
                               "parts": [{"kind": "data", "data": data}], "messageId": "m1"}},
    }).json()


def test_agent_card():
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "agent-evals"
    assert any(s["id"] == "eval-verdict" for s in card["skills"])


def test_never_evaluated_version_is_not_passing():
    r = _verdict({"agent": "ranker", "version": "v999"})
    data = r["result"]["status"]["message"]["parts"][1]["data"]
    assert data["passed"] is False and "not evaluated" in data["failures"]


def test_verdict_reflects_a_recorded_run():
    # Drive a passing eval through the HTTP endpoint, then resolve it over A2A.
    run = client.post("/v1/evals/run", json={
        "agent": "ranker", "prompt_version": "v1",
        "metrics": {"accuracy": 0.99, "hallucination_rate": 0.0, "latency_p95_ms": 100},
    }).json()
    r = _verdict({"agent": "ranker", "version": "v1"}, req_id="2")
    data = r["result"]["status"]["message"]["parts"][1]["data"]
    assert data["passed"] == run["passed"]


def test_missing_fields_is_invalid_params():
    r = _verdict({"agent": "ranker"}, req_id="3")
    assert r["error"]["code"] == -32602
