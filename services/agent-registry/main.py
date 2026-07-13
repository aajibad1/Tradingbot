"""agent-registry — agents + immutable prompt/model versions (docs/10).

Source of truth for "what agents exist and which version is live." Prompt versions
are immutable (a version's content can never be silently changed — that would break
traceability). Activating a version is GATED on a passing eval verdict from
agent-evals: an un-evaluated or failing version cannot go live.

Registry only — it stores and gates; it never runs an agent.

Config: AGENT_EVALS_URL (when set, activation consults the eval verdict; when unset,
activation is allowed — sandbox/bootstrap mode).

Endpoints:
  GET  /healthz
  POST /v1/agents                         register an agent
  GET  /v1/agents
  GET  /v1/agents/{name}
  POST /v1/agents/{name}/prompts          add an immutable prompt version
  GET  /v1/agents/{name}/prompts
  POST /v1/agents/{name}/activate         {version}  (gated on eval verdict)
  GET  /v1/agents/{name}/active
  GET  /v1/a2a/catalog                    live A2A roster catalog (browsable mesh view)
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from shared.a2a import (
    A2AClient,
    A2AError,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Message,
    base_url,
    client_for,
    data_part,
    discover_agents,
    install_a2a,
    roster_catalog,
    text_part,
)
from shared.a2a import errors as a2a_errors
from shared.http import APIError, install_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("agent-registry")

PRODUCER = "agent-registry"

# name -> agent dict; agent["prompts"][version] = prompt dict; agent["active"] = version|None
_agents: dict[str, dict] = {}

app = FastAPI(title="agent-registry", version="0.1.0")
install_contract(app, service_name=PRODUCER)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    provider: str = Field(default="claude", description="claude | perplexity | ...")
    default_model: str | None = None


class PromptVersion(BaseModel):
    version: str = Field(description="e.g. 'v1', '2026-06-10'")
    content: str = Field(description="the prompt text (hashed for integrity)")
    notes: str = ""


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/agents")
def create_agent(req: AgentCreate) -> dict[str, Any]:
    if req.name in _agents:
        raise APIError("agent_exists", f"agent {req.name} already registered", http_status=409)
    _agents[req.name] = {
        "name": req.name, "description": req.description, "provider": req.provider,
        "default_model": req.default_model, "prompts": {}, "active": None, "created_at": _now(),
    }
    return _public(_agents[req.name])


def _public(agent: dict) -> dict:
    return {**{k: v for k, v in agent.items() if k != "prompts"},
            "prompt_versions": sorted(agent["prompts"].keys())}


def _get(name: str) -> dict:
    a = _agents.get(name)
    if a is None:
        raise APIError("agent_not_found", f"agent {name} not found", http_status=404)
    return a


@app.get("/v1/agents")
def list_agents() -> dict[str, Any]:
    return {"agents": [_public(a) for a in _agents.values()], "count": len(_agents)}


@app.get("/v1/a2a/catalog")
def a2a_catalog() -> dict[str, Any]:
    """Browse the live A2A roster: fetch every known agent's card and project the
    capability catalog over plain HTTP. Best-effort — an agent that is down is
    listed under ``unreachable`` rather than failing the request — so this doubles
    as an ops view of which agents are answering. Distinct from ``/v1/agents``,
    which lists this registry's governance-versioned agents, not the live A2A mesh."""
    return roster_catalog(client_factory=lambda n: client_for(n, timeout=2.0))


@app.get("/v1/agents/{name}")
def get_agent(name: str) -> dict[str, Any]:
    return _public(_get(name))


@app.post("/v1/agents/{name}/prompts")
def add_prompt(name: str, req: PromptVersion) -> dict[str, Any]:
    agent = _get(name)
    if req.version in agent["prompts"]:
        raise APIError("version_exists",
                       f"version {req.version} already exists (prompt versions are immutable)",
                       http_status=409)
    record = {
        "version": req.version,
        "content_hash": hashlib.sha256(req.content.encode("utf-8")).hexdigest(),
        "notes": req.notes,
        "created_at": _now(),
    }
    agent["prompts"][req.version] = record
    return record


@app.get("/v1/agents/{name}/prompts")
def list_prompts(name: str) -> dict[str, Any]:
    agent = _get(name)
    return {"agent": name, "prompts": list(agent["prompts"].values()), "active": agent["active"]}


def _resolve_evals_base() -> tuple[str | None, bool]:
    """Locate the eval authority's A2A base URL. An explicit env wins (back-compat
    and deliberate pinning); otherwise discover whoever advertises the
    ``eval-verdict`` skill on the A2A roster. Discovery is time-bounded so a dead
    peer can't stall an activation.

    Returns ``(base_url_or_none, mesh_reachable)``. The second element is how the
    caller distinguishes a true sandbox (NOTHING on the roster answered — no mesh
    deployed) from a running mesh whose eval authority is merely down or missing.
    The two must not be conflated: treating an evals blip as "not configured"
    would silently flip the eval gate OPEN during an outage."""
    explicit = os.environ.get("A2A_AGENT_EVALS_URL") or os.environ.get("AGENT_EVALS_URL")
    if explicit:
        return explicit, True
    cards = discover_agents(client_factory=lambda n: client_for(n, timeout=2.0))
    providers = sorted(
        name for name, card in cards.items()
        if any(skill.id == "eval-verdict" for skill in card.skills)
    )
    if providers:
        return base_url(providers[0]), True
    return None, bool(cards)


def _eval_passed(agent: str, version: str) -> tuple[bool, str]:
    """Consult agent-evals for a passing verdict over A2A (the agent↔agent
    contract). The peer is resolved by ``_resolve_evals_base`` — explicit env, else
    capability discovery. When evals isn't wired at all we allow activation
    (sandbox/bootstrap). Fail-CLOSED on a reachable-but-not-passing or un-evaluated
    version. The legacy AGENT_EVALS_URL still works: A2A's endpoint lives at
    {base}/a2a on the same agent-evals service.

    Sandbox-allow applies ONLY when no mesh is deployed at all (no roster member
    answered discovery). A reachable mesh with no discoverable eval authority
    fails CLOSED — an agent-evals outage must never become an activation bypass."""
    base, mesh_reachable = _resolve_evals_base()
    if not base:
        if mesh_reachable:
            return False, "eval authority not discoverable (fail closed)"
        return True, "evals not configured (sandbox)"
    try:
        task = A2AClient(base, timeout=5.0).send_data({"agent": agent, "version": version})
    except Exception:
        return False, "eval service unreachable"
    reply = task.status.message
    data = (reply.data() if reply is not None else None) or {}
    if data.get("passed"):
        return True, "eval passed"
    if data.get("failures") == ["not evaluated"]:
        return False, "version not evaluated"
    return False, f"eval failed: {data.get('failures')}"


@app.post("/v1/agents/{name}/activate")
def activate(name: str, body: dict[str, Any]) -> dict[str, Any]:
    agent = _get(name)
    version = body.get("version")
    if version not in agent["prompts"]:
        raise APIError("version_not_found", f"{name} has no version {version}", http_status=404)
    passed, reason = _eval_passed(name, version)
    if not passed:
        raise APIError("eval_not_passed",
                       f"cannot activate {name} {version}: {reason}", http_status=409)
    agent["active"] = version
    logger.info("activated agent=%s version=%s (%s)", name, version, reason)
    return {"agent": name, "active": version, "reason": reason}


@app.get("/v1/agents/{name}/active")
def get_active(name: str) -> dict[str, Any]:
    agent = _get(name)
    return {"agent": name, "active": agent["active"]}


# --- A2A (Agent2Agent) surface ------------------------------------------------
# The registry of agents + immutable prompt/model versions (docs/10) exposed over
# A2A: a peer asks which version of an agent is active (so it can pin the right,
# eval-gated prompt/model). Read-only resolution — activation stays HTTP-gated.
_A2A_CARD = AgentCard(
    name="agent-registry",
    description=("Registry of agents and their immutable prompt/model versions. "
                 "Resolve the currently-active (eval-gated) version of any agent."),
    url=f"{base_url('agent-registry')}/a2a",
    version=app.version,
    provider=AgentProvider(organization="traditbot"),
    skills=[AgentSkill(
        id="resolve-active-version",
        name="Resolve active version",
        description=("Given an agent name, return its active prompt version and the "
                     "list of known versions."),
        tags=["governance", "registry", "versioning", "discovery"],
        examples=['{"agent":"opportunity-ranker"}', "opportunity-ranker"],
    )],
)


def _a2a_handle(message: Message):
    data = message.data() or {}
    name = (data.get("agent") or message.text()).strip()
    if not name:
        raise A2AError(a2a_errors.INVALID_PARAMS,
                       "agent name required (data {'agent': name} or text)")
    try:
        info = _public(_get(name))
    except APIError as exc:
        raise A2AError(a2a_errors.INVALID_PARAMS, f"{exc.code}: {exc.message}") from exc
    result = {"agent": name, "active": info["active"],
              "prompt_versions": info.get("prompt_versions", [])}
    summary = f"{name} active={info['active'] or '(none)'}"
    return [text_part(summary), data_part(result)]


install_a2a(app, card=_A2A_CARD, handler=_a2a_handle)
