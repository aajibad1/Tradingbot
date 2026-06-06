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
