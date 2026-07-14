"""Corridor-alert → ops-notification consumer."""

from __future__ import annotations

import json

import main
from corridor import corridor_notification
from models import Channel, Priority


def test_alert_maps_to_country_routed_ops_notification():
    note = corridor_notification(
        {"corridor": "NGN->ZAR", "net_edge_bps": 180.0, "settlement_confidence": 0.82}
    )
    assert note.user_id == "ops"
    assert note.country == "NG"               # source currency NGN → NG
    assert "NGN->ZAR" in note.message
    assert "180 bps" in note.message
    assert note.priority is Priority.NORMAL


def test_unknown_corridor_has_empty_country():
    note = corridor_notification({"corridor": "weird", "net_edge_bps": 120})
    assert note.country == ""


def test_recipients_are_passed_through():
    note = corridor_notification({"corridor": "KES->ZAR"}, {Channel.TELEGRAM: "chat-1"})
    assert note.recipients[Channel.TELEGRAM] == "chat-1"
    assert note.country == "KE"


def test_on_corridor_alert_dispatches(monkeypatch):
    captured = {}

    def fake_dispatch(notification, providers):
        captured["note"] = notification
        return None

    monkeypatch.setattr(main.dispatcher_mod, "dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_build_providers", lambda: {})

    class _Msg:
        def __init__(self, d):
            self.data = json.dumps(d).encode()
            self.acked = False

        def ack(self):
            self.acked = True

    msg = _Msg({"corridor": "GHS->NGN", "net_edge_bps": 140.0, "settlement_confidence": 0.7})
    main._on_corridor_alert(msg)

    assert msg.acked
    assert captured["note"].country == "GH"
    assert "GHS->NGN" in captured["note"].message


def test_bad_payload_is_acked_not_crashing(monkeypatch):
    monkeypatch.setattr(main.dispatcher_mod, "dispatch", lambda *a, **k: None)

    class _Msg:
        data = b"not-json{"

        def __init__(self):
            self.acked = False

        def ack(self):
            self.acked = True

    msg = _Msg()
    main._on_corridor_alert(msg)
    assert msg.acked


def test_evidence_block_renders_receipts():
    note = corridor_notification({
        "corridor": "NGN->ZAR", "net_edge_bps": 160.0, "settlement_confidence": 0.7,
        "breakdown": {
            "intel": {"venue_reliability": 0.7, "source": "perplexity",
                      "sources": ["https://a", "https://b", "https://c"],
                      "model_version": "sonar-pro"},
            "debate": {"decision": "support", "confidence": 0.8,
                       "refuted_by": 0, "n_skeptics": 3},
            "fx_benchmark": {"official_fx_dest_per_source": 0.01125,
                             "source": "fx-rate-service:official"},
        },
    })
    assert "Evidence: reliability 0.70 (perplexity, 3 sources)" in note.message
    assert "debate: support 0.80, 0/3 skeptics refuted" in note.message
    assert "FX benchmark: fx-rate-service:official" in note.message


def test_alert_without_breakdown_renders_exactly_as_before():
    note = corridor_notification(
        {"corridor": "NGN->ZAR", "net_edge_bps": 180.0, "settlement_confidence": 0.82})
    assert "Evidence" not in note.message and "\n" not in note.message


def test_partial_evidence_renders_only_present_parts():
    note = corridor_notification({
        "corridor": "KES->ZAR", "net_edge_bps": 120.0, "settlement_confidence": 0.6,
        "breakdown": {"intel": {"venue_reliability": 0.6, "source": "baseline"}},
    })
    assert "Evidence: reliability 0.60 (baseline)" in note.message
    assert "debate" not in note.message and "FX benchmark" not in note.message


def test_malformed_breakdown_never_blocks_the_alert():
    # Evidence is decoration: any garbage upstream (misbehaving debate peer,
    # rogue publisher) must degrade to "no evidence line", never raise — an
    # exception here would nack-loop the alerts subscription.
    malformed = [
        {"intel": {"venue_reliability": None}},                    # TypeError path
        {"intel": {"venue_reliability": "high"}},                  # ValueError path
        {"intel": "not-a-dict"},                                   # AttributeError path
        {"debate": {"decision": "support", "confidence": "high"}}, # non-numeric conf
        "not-a-dict-at-all",                                       # breakdown non-dict
        ["still", "not", "a", "dict"],
    ]
    for bd in malformed:
        note = corridor_notification(
            {"corridor": "NGN->ZAR", "net_edge_bps": 160.0,
             "settlement_confidence": 0.7, "breakdown": bd})
        assert note.message.startswith("Corridor NGN->ZAR: +160 bps net"), bd


def test_partial_debate_skips_skeptics_not_renders_none():
    # refuted_by=None with n_skeptics set must not render "None/3 skeptics".
    note = corridor_notification({
        "corridor": "NGN->ZAR", "net_edge_bps": 160.0, "settlement_confidence": 0.7,
        "breakdown": {"debate": {"decision": "support", "confidence": 0.8,
                                 "refuted_by": None, "n_skeptics": 3}},
    })
    assert "debate: support 0.80" in note.message
    assert "None/" not in note.message and "skeptics" not in note.message
