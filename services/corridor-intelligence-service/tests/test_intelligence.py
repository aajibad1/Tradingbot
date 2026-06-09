"""Corridor-intelligence tests — baseline + injected Perplexity research."""
from __future__ import annotations

from intelligence import assess_corridor, source_country


def test_baseline_for_launch_country():
    intel = assess_corridor("NGN->ZAR")           # no research → baseline
    assert intel.source == "baseline"
    assert intel.reliability_score == 0.7
    assert intel.regulatory_risk == "medium"


def test_baseline_for_blocked_country():
    # source currency unknown maps to '' ; use a blocked-country-style: simulate via research None
    intel = assess_corridor("XXX->ZAR")
    assert intel.source == "baseline"
    assert intel.reliability_score == 0.5  # unknown source → review baseline


def test_research_result_is_used_and_clamped():
    def fake_research(corridor):
        return {"reliability": 1.5, "friction_bps": 22.0, "regulatory_risk": "LOW",
                "summary": "stable corridor",
                "citations": ["https://example.com/policy"], "model_version": "sonar-pro"}
    intel = assess_corridor("NGN->ZAR", research=fake_research)
    assert intel.source == "perplexity"
    assert intel.reliability_score == 1.0          # clamped
    assert intel.regulatory_risk == "low"
    assert intel.settlement_friction_bps == 22.0
    # grounding/audit: citations + model carried through
    assert intel.sources == ["https://example.com/policy"]
    assert intel.model_version == "sonar-pro"


def test_baseline_has_no_sources_or_model():
    intel = assess_corridor("NGN->ZAR")
    assert intel.sources == [] and intel.model_version is None


def test_research_none_falls_back_to_baseline():
    intel = assess_corridor("KES->ZAR", research=lambda c: None)
    assert intel.source == "baseline"


def test_bad_regulatory_risk_defaults_high():
    intel = assess_corridor("GHS->NGN", research=lambda c: {"reliability": 0.6, "regulatory_risk": "weird"})
    assert intel.regulatory_risk == "high"


def test_source_country_mapping():
    assert source_country("NGN->ZAR") == "NG"
    assert source_country("nonsense") == ""


def test_perplexity_client_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    from clients import perplexity_research
    assert perplexity_research.assess("NGN->ZAR") is None


def test_assess_endpoint_baseline(monkeypatch):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    assert c.get("/healthz").json()["status"] == "ok"
    out = c.post("/assess", json={"corridor": "NGN->ZAR"}).json()
    assert out["source"] == "baseline" and out["reliability_score"] == 0.7
