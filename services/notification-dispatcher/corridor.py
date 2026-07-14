"""Map a corridor alert (from corridor-engine) to an ops Notification.

Corridor opportunities are alert-only: a viable corridor is a signal for a human
operator to settle, so it routes to the ops channel, country-tagged by the source
currency for channel routing.
"""

from __future__ import annotations

from models import Channel, Notification, Priority

# Source currency → ISO country (drives channel routing).
_CCY_COUNTRY = {"NGN": "NG", "ZAR": "ZA", "KES": "KE", "GHS": "GH", "USD": ""}


def _source_country(corridor: str) -> str:
    src = corridor.split("->", 1)[0].strip() if "->" in corridor else ""
    return _CCY_COUNTRY.get(src.upper(), "")


def _evidence_line(breakdown: dict) -> str:
    """Render the alert's receipts — the operator decides whether to move money,
    so the WHY (intel reliability + citations, adversarial debate verdict, FX
    benchmark provenance) leads alongside the number. Every part is optional;
    an alert with no evidence renders exactly as before."""
    parts: list[str] = []
    intel = breakdown.get("intel") or {}
    if intel:
        src = intel.get("source", "intel")
        n_sources = len(intel.get("sources") or [])
        cite = f", {n_sources} source{'s' if n_sources != 1 else ''}" if n_sources else ""
        parts.append(f"reliability {float(intel.get('venue_reliability', 0.0)):.2f} ({src}{cite})")
    debate = breakdown.get("debate") or {}
    if debate.get("decision"):
        conf = debate.get("confidence")
        conf_s = f" {float(conf):.2f}" if conf is not None else ""
        refuted, skeptics = debate.get("refuted_by"), debate.get("n_skeptics")
        panel = f", {refuted}/{skeptics} skeptics refuted" if skeptics else ""
        parts.append(f"debate: {debate['decision']}{conf_s}{panel}")
    fx = breakdown.get("fx_benchmark") or {}
    if fx.get("source"):
        parts.append(f"FX benchmark: {fx['source']}")
    return " · ".join(parts)


def corridor_notification(alert: dict, recipients: dict[Channel, str] | None = None) -> Notification:
    corridor = str(alert.get("corridor", "?"))
    net = float(alert.get("net_edge_bps", 0.0))
    conf = float(alert.get("settlement_confidence", 0.0))
    msg = (
        f"Corridor {corridor}: +{net:.0f} bps net, settlement confidence "
        f"{conf:.0%} — review and settle (alert-only)."
    )
    evidence = _evidence_line(alert.get("breakdown") or {})
    if evidence:
        msg += f"\nEvidence: {evidence}"
    return Notification(
        user_id="ops",
        country=_source_country(corridor),
        message=msg,
        priority=Priority.NORMAL,
        recipients=recipients or {},
    )
