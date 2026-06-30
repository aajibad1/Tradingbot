"""A2A (Agent2Agent) — the agent↔agent contract for this platform.

One import wires a FastAPI agent into A2A:

    from shared.a2a import install_a2a, AgentCard, AgentSkill, text_part
    install_a2a(app, card=AgentCard(...), handler=lambda m: [text_part(reply(m.text()))])

…serving ``GET /.well-known/agent-card.json`` + ``POST /a2a`` (JSON-RPC 2.0:
message/send, tasks/get, tasks/cancel). Call a peer with ``A2AClient`` /
``client_for(name)``. Complements MCP (agent↔tool); see docs/10."""
from __future__ import annotations

from . import errors
from .client import A2AClient
from .errors import A2AError
from .models import (
    PROTOCOL_VERSION,
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Artifact,
    DataPart,
    JSONRPCRequest,
    Message,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
    data_part,
    rpc_error,
    rpc_result,
    text_part,
)
from .registry import (
    A2A_AGENTS,
    base_url,
    client_for,
    discover_agents,
    find_agents_with_skill,
    roster_catalog,
)
from .server import AGENT_CARD_PATH, Handler, install_a2a
from .store import InMemoryTaskStore

__all__ = [
    "install_a2a", "Handler", "AGENT_CARD_PATH",
    "AgentCard", "AgentSkill", "AgentCapabilities", "AgentProvider",
    "Message", "Task", "TaskState", "TaskStatus", "Artifact",
    "TextPart", "DataPart", "text_part", "data_part",
    "JSONRPCRequest", "rpc_result", "rpc_error", "PROTOCOL_VERSION",
    "InMemoryTaskStore",
    "A2AClient", "A2AError", "errors",
    "base_url", "client_for", "A2A_AGENTS",
    "discover_agents", "find_agents_with_skill", "roster_catalog",
]
