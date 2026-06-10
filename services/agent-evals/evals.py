"""Eval scoring for agent prompt/model versions (docs/10). Pure + testable.

Doc 10 requires accuracy / hallucination / latency / post-outcome review before an
AI version goes live. This scores a metrics bundle against thresholds and returns a
pass/fail verdict + the specific failures, so a version can't be promoted to active
in the registry until it passes (the eval→promotion gate).
"""

from __future__ import annotations

# Default promotion thresholds. Conservative — tighten with real data.
DEFAULT_THRESHOLDS = {
    "min_accuracy": 0.80,           # fraction correct vs labels (higher better)
    "max_hallucination_rate": 0.05,  # fraction unsupported claims (lower better)
    "max_latency_ms": 5000,         # p95 latency (lower better)
}


def evaluate(metrics: dict, thresholds: dict | None = None) -> tuple[bool, list[str]]:
    """Return ``(passed, failures)`` for a metrics bundle.

    A missing metric is itself a failure — you cannot promise a version is safe on
    data you never measured."""
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[str] = []

    acc = metrics.get("accuracy")
    if acc is None:
        failures.append("accuracy not measured")
    elif acc < t["min_accuracy"]:
        failures.append(f"accuracy {acc} < {t['min_accuracy']}")

    hr = metrics.get("hallucination_rate")
    if hr is None:
        failures.append("hallucination_rate not measured")
    elif hr > t["max_hallucination_rate"]:
        failures.append(f"hallucination_rate {hr} > {t['max_hallucination_rate']}")

    lat = metrics.get("latency_ms")
    if lat is None:
        failures.append("latency_ms not measured")
    elif lat > t["max_latency_ms"]:
        failures.append(f"latency_ms {lat} > {t['max_latency_ms']}")

    return (len(failures) == 0), failures
