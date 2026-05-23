"""ai-ops-agent — FastAPI + MCP.

Cloud Run mode: exposes a thin HTTP surface that mirrors the MCP tool catalog
(useful for testing, dashboards, and non-MCP callers). The MCP server itself
runs over stdio when invoked from the CLI; HTTP/SSE transport for remote
Claude/Gemini clients can be wired later if needed.

Endpoints:
  GET  /healthz
  GET  /tools                       — list registered tools (NEVER tools NOT advertised)
  POST /tools/{tool_name}/invoke    — invoke a tool (permission-gated)

CRITICAL INVARIANT: NEVER-tier tools are NOT discoverable. They are not
returned by /tools and they are not registered with the MCP `list_tools`
handler. Any attempt to invoke one — by name, via any transport — fails
with ToolBlockedError. This is enforced at three layers:
  1. mcp_server.list_tools() filters NEVER out of the advertised catalog
  2. permissions.require() raises ToolBlockedError on NEVER-tier
  3. NEVER tools have NO implementation registered in _TOOL_IMPLS
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from mcp_server import _TOOL_IMPLS, call_tool, list_tools
from permissions import ToolBlockedError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ai-ops-agent")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("ai-ops-agent starting; %d tools registered", len(_TOOL_IMPLS))
    yield
    logger.info("ai-ops-agent shutting down")


app = FastAPI(title="ai-ops-agent", version="0.1.0", lifespan=lifespan)


class InvokeRequest(BaseModel):
    args: dict[str, Any] = {}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/tools")
def list_tools_endpoint() -> dict[str, Any]:
    """Advertise the MCP tool catalog. NEVER-tier tools are NOT included."""
    return {"tools": list_tools()}


@app.post("/tools/{tool_name}/invoke")
def invoke(tool_name: str, req: InvokeRequest) -> Any:
    if tool_name not in _TOOL_IMPLS:
        # Tool either doesn't exist OR is a NEVER-tier tool we refuse to even
        # acknowledge as discoverable. Either way: 404.
        raise HTTPException(status_code=404, detail=f"unknown tool {tool_name!r}")
    try:
        return call_tool(tool_name, req.args)
    except ToolBlockedError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
