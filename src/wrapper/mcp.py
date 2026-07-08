"""Builders for MCP server connections and hosted MCP tools."""

from __future__ import annotations

import os
from typing import Any

from agents import HostedMCPTool
from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter

from src.wrapper.approvals import approve_tool

DEFAULT_MCP_URL = "http://localhost:8000/mcp"
DEFAULT_TIMEOUT_SECONDS = 30

#: Tools exposed by the local stub server (see ``src/mcp_stub_server.py``).
STUB_TOOL_NAMES: tuple[str, ...] = (
    "search_docs",
    "read_metrics",
    "create_task",
    "update_ticket",
    "save_memory",
    "get_memory",
)

_VALID_APPROVAL_MODES = ("always", "never")


def build_local_mcp_server() -> MCPServerStreamableHttp:
    """Build a Streamable HTTP MCP server connection from ``MCP_*`` env vars."""
    mcp_url = os.getenv("MCP_URL", DEFAULT_MCP_URL)
    timeout = int(os.getenv("MCP_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))

    params: dict[str, Any] = {
        "url": mcp_url,
        "timeout": timeout,
    }
    mcp_token = os.getenv("MCP_TOKEN", "").strip()
    if mcp_token:
        params["headers"] = {"Authorization": f"Bearer {mcp_token}"}

    return MCPServerStreamableHttp(
        name="capabilities",
        params=params,
        cache_tools_list=True,
        client_session_timeout_seconds=timeout,
        max_retry_attempts=3,
        tool_filter=create_static_tool_filter(
            allowed_tool_names=list(STUB_TOOL_NAMES),
        ),
    )


def build_hosted_mcp_tool() -> HostedMCPTool:
    """Build a hosted MCP tool with the approval hook attached.

    Requires ``MCP_HOSTED_URL``; ``MCP_REQUIRE_APPROVAL`` must be one of
    ``always`` / ``never`` (defaults to ``always``).
    """
    server_url = os.getenv("MCP_HOSTED_URL", "")
    if not server_url:
        raise ValueError("MCP_HOSTED_URL is required for hosted MCP tools")

    require_approval = os.getenv("MCP_REQUIRE_APPROVAL", "always").strip().lower()
    if require_approval not in _VALID_APPROVAL_MODES:
        raise ValueError(
            f"MCP_REQUIRE_APPROVAL must be one of {_VALID_APPROVAL_MODES}, "
            f"got {require_approval!r}"
        )

    return HostedMCPTool(
        tool_config={
            "type": "mcp",
            "server_label": os.getenv("MCP_HOSTED_LABEL", "capabilities"),
            "server_url": server_url,
            "require_approval": require_approval,
        },
        on_approval_request=approve_tool,
    )
