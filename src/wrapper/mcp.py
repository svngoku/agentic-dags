from __future__ import annotations

import os
from typing import Any, Dict

from agents import HostedMCPTool
from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter

from src.wrapper.approvals import approve_tool


def build_local_mcp_server() -> MCPServerStreamableHttp:
    mcp_url = os.getenv("MCP_URL", "http://localhost:8000/mcp")
    params: Dict[str, Any] = {
        "url": mcp_url,
        "timeout": 30,
    }
    mcp_token = os.getenv("MCP_TOKEN", "").strip()
    if mcp_token:
        params["headers"] = {"Authorization": f"Bearer {mcp_token}"}

    return MCPServerStreamableHttp(
        name="capabilities",
        params=params,
        cache_tools_list=True,
        client_session_timeout_seconds=30,
        max_retry_attempts=3,
        tool_filter=create_static_tool_filter(
            allowed_tool_names=[
                "search_docs",
                "read_metrics",
                "create_task",
                "update_ticket",
                "save_memory",
                "get_memory",
            ]
        ),
    )


def build_hosted_mcp_tool() -> HostedMCPTool:
    server_url = os.getenv("MCP_HOSTED_URL", "")
    if not server_url:
        raise ValueError("MCP_HOSTED_URL is required for hosted MCP tools")
    require_approval = os.getenv("MCP_REQUIRE_APPROVAL", "always")
    return HostedMCPTool(
        tool_config={
            "type": "mcp",
            "server_label": os.getenv("MCP_HOSTED_LABEL", "capabilities"),
            "server_url": server_url,
            "require_approval": require_approval,
        },
        on_approval_request=approve_tool,
    )
