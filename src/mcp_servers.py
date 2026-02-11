from __future__ import annotations

from agents.mcp import MCPServerStreamableHttp

from src.wrapper.mcp import build_local_mcp_server


def build_mcp_server() -> MCPServerStreamableHttp:
    return build_local_mcp_server()
