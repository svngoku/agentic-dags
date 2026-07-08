"""Entry point for building the MCP server used by the flow.

Thin indirection over :mod:`src.wrapper.mcp` so flows depend on a stable
import path even if the wrapper internals move.
"""

from __future__ import annotations

from agents.mcp import MCPServerStreamableHttp

from src.wrapper.mcp import build_local_mcp_server


def build_mcp_server() -> MCPServerStreamableHttp:
    """Build the local Streamable HTTP MCP server connection."""
    return build_local_mcp_server()
