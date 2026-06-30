"""A2A discovery — resolve a peer agent's base URL by name.

The roster of A2A-speaking agents is small and known at build time, so discovery
is a static name→URL map with per-agent env overrides (``A2A_<NAME>_URL``, where
NAME is the upper-snake agent name). This mirrors how the rest of the platform
resolves peers (no service mesh locally); a service-registry backend can replace
``base_url`` later without changing call sites.

    from shared.a2a import A2AClient, base_url
    verdict = A2AClient(base_url("debate-service")).send_text("funding carry is real")
"""
from __future__ import annotations

import os

from .client import A2AClient

# Default local ports for the A2A-speaking agent services (used by local_stack).
_DEFAULT_PORTS: dict[str, int] = {
    "debate-service": 8340,
    "approval-gate-service": 8341,
    "agent-registry": 8342,
    "agent-evals": 8343,
    "ai-ops-agent": 8344,
}

A2A_AGENTS = frozenset(_DEFAULT_PORTS)


def _env_key(name: str) -> str:
    return "A2A_" + name.upper().replace("-", "_") + "_URL"


def base_url(name: str) -> str:
    """Resolve an agent's A2A base URL: ``A2A_<NAME>_URL`` env override, else the
    local default (``http://localhost:<port>``). Raises for an unknown agent so a
    typo fails loud rather than silently pointing nowhere."""
    override = os.environ.get(_env_key(name))
    if override:
        return override.rstrip("/")
    if name not in _DEFAULT_PORTS:
        raise KeyError(f"unknown A2A agent: {name!r}")
    return f"http://localhost:{_DEFAULT_PORTS[name]}"


def client_for(name: str, **kwargs) -> A2AClient:
    """A2AClient pointed at the named agent (convenience over ``base_url``)."""
    return A2AClient(base_url(name), **kwargs)


def discover_agents(names=None, *, client_factory=None) -> dict:
    """Fetch the agent-card of every roster agent (or a given subset) and return
    ``{name: AgentCard}`` for those that answer.

    Discovery is best-effort: an agent that is down, slow, or returns a malformed
    card is omitted rather than raising — the same fail-soft stance the rest of the
    platform takes toward a missing peer. A typo, however, fails loud: an unknown
    agent name raises ``KeyError`` (via ``base_url``) instead of being swallowed.

    ``client_factory`` (name -> A2AClient) is an injection point for tests, letting
    them point discovery at in-process apps without a live server; defaults to
    ``client_for``."""
    factory = client_factory or client_for
    roster = sorted(A2A_AGENTS) if names is None else list(names)
    cards: dict = {}
    for name in roster:
        try:
            cards[name] = factory(name).fetch_card()
        except KeyError:
            raise  # unknown agent — a programming typo, not a transient outage
        except Exception:
            continue  # unreachable / malformed card — best-effort, skip it
    return cards


def find_agents_with_skill(skill_id: str, *, names=None, client_factory=None) -> list:
    """Names of roster agents whose card advertises a skill with ``skill_id`` — route
    a request to whoever can handle it instead of hardcoding the peer. Returns a
    sorted list; best-effort, so unreachable agents simply don't appear."""
    cards = discover_agents(names=names, client_factory=client_factory)
    return sorted(
        name for name, card in cards.items()
        if any(skill.id == skill_id for skill in card.skills)
    )


def roster_catalog(*, names=None, client_factory=None) -> dict:
    """A browsable projection of the live A2A roster — discover every agent's card
    and return ``{agents, count, roster, unreachable}``. ``agents`` carries each
    reachable agent's name/description/version/url/skills; ``unreachable`` lists the
    roster members that didn't answer (an ops view). Best-effort, like
    ``discover_agents``. The single place the catalog shape is defined, so every
    surface that exposes the mesh (registry endpoint, status page) agrees."""
    cards = discover_agents(names=names, client_factory=client_factory)
    agents = [
        {"name": name, "description": card.description, "version": card.version,
         "url": card.url,
         "skills": [{"id": s.id, "name": s.name, "description": s.description}
                    for s in card.skills]}
        for name, card in sorted(cards.items())
    ]
    roster = sorted(A2A_AGENTS) if names is None else sorted(names)
    return {"agents": agents, "count": len(agents), "roster": roster,
            "unreachable": sorted(n for n in roster if n not in cards)}
