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


def corridor_notification(alert: dict, recipients: dict[Channel, str] | None = None) -> Notification:
    corridor = str(alert.get("corridor", "?"))
    net = float(alert.get("net_edge_bps", 0.0))
    conf = float(alert.get("settlement_confidence", 0.0))
    msg = (
        f"Corridor {corridor}: +{net:.0f} bps net, settlement confidence "
        f"{conf:.0%} — review and settle (alert-only)."
    )
    return Notification(
        user_id="ops",
        country=_source_country(corridor),
        message=msg,
        priority=Priority.NORMAL,
        recipients=recipients or {},
    )
