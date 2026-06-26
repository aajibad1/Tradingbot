"""A2A (Agent2Agent) wire models — the agent↔agent contract (complements MCP's
agent↔tool contract).

Spec-faithful subset of the Agent2Agent protocol: an Agent Card advertises an
agent's identity + skills; work flows as JSON-RPC 2.0 calls (`message/send`,
`tasks/get`, `tasks/cancel`) carrying Messages and producing Tasks/Artifacts.

The wire format is camelCase (`messageId`, `contextId`, `protocolVersion`, …);
Python fields stay snake_case and serialize via an alias generator. Always emit
with ``model_dump(by_alias=True, exclude_none=True)`` and parse permissively
(``populate_by_name=True`` accepts either casing)."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PROTOCOL_VERSION = "0.3.0"


class _A2AModel(BaseModel):
    """camelCase on the wire, snake_case in Python; tolerant on input."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    def wire(self) -> dict:
        """Canonical wire dict: camelCase keys, drop unset optionals."""
        return self.model_dump(by_alias=True, exclude_none=True)


# --- Parts (a Message/Artifact is a list of typed parts) ----------------------
class TextPart(_A2AModel):
    kind: Literal["text"] = "text"
    text: str


class DataPart(_A2AModel):
    kind: Literal["data"] = "data"
    data: dict[str, Any]


Part = Annotated[Union[TextPart, DataPart], Field(discriminator="kind")]


def text_part(text: str) -> TextPart:
    return TextPart(text=text)


def data_part(data: dict[str, Any]) -> DataPart:
    return DataPart(data=data)


# --- Messages, Tasks, Artifacts ----------------------------------------------
class Message(_A2AModel):
    kind: Literal["message"] = "message"
    role: Literal["user", "agent"]
    parts: list[Part]
    message_id: str
    task_id: Optional[str] = None
    context_id: Optional[str] = None

    def text(self) -> str:
        """Concatenate all text parts — the common 'what did they say' accessor."""
        return "\n".join(p.text for p in self.parts if isinstance(p, TextPart))

    def data(self) -> Optional[dict[str, Any]]:
        """First structured (data) part, or None — the 'what did they ask for'
        accessor for agents that take typed input rather than free text."""
        for p in self.parts:
            if isinstance(p, DataPart):
                return p.data
        return None


class TaskState:
    """A2A task lifecycle states."""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"
    UNKNOWN = "unknown"

    TERMINAL = frozenset({COMPLETED, CANCELED, FAILED, REJECTED})


class TaskStatus(_A2AModel):
    state: str
    message: Optional[Message] = None
    timestamp: Optional[str] = None


class Artifact(_A2AModel):
    artifact_id: str
    name: Optional[str] = None
    parts: list[Part]


class Task(_A2AModel):
    kind: Literal["task"] = "task"
    id: str
    context_id: str
    status: TaskStatus
    history: Optional[list[Message]] = None
    artifacts: Optional[list[Artifact]] = None


# --- Agent Card (served at /.well-known/agent-card.json) ----------------------
class AgentProvider(_A2AModel):
    organization: str
    url: Optional[str] = None


class AgentCapabilities(_A2AModel):
    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False


class AgentSkill(_A2AModel):
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: Optional[list[str]] = None
    input_modes: Optional[list[str]] = None
    output_modes: Optional[list[str]] = None


class AgentCard(_A2AModel):
    name: str
    description: str
    url: str
    version: str
    protocol_version: str = PROTOCOL_VERSION
    preferred_transport: str = "JSONRPC"
    provider: Optional[AgentProvider] = None
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    skills: list[AgentSkill] = Field(default_factory=list)


# --- JSON-RPC 2.0 envelope ----------------------------------------------------
class JSONRPCRequest(_A2AModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Union[str, int, None] = None
    method: str
    params: Optional[dict[str, Any]] = None


class JSONRPCErrorObject(_A2AModel):
    code: int
    message: str
    data: Optional[Any] = None


def rpc_result(req_id: Union[str, int, None], result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id: Union[str, int, None], code: int, message: str,
              data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}
