"""Approval policy for hosted MCP tool calls."""

from __future__ import annotations

from agents import MCPToolApprovalFunctionResult, MCPToolApprovalRequest

#: Read-only tools that are always safe to auto-approve.
SAFE_TOOLS: frozenset[str] = frozenset({"search_docs", "read_metrics"})


def approve_tool(request: MCPToolApprovalRequest) -> MCPToolApprovalFunctionResult:
    """Auto-approve read-only tools; escalate everything else for human review."""
    if request.data.name in SAFE_TOOLS:
        return {"approve": True}
    return {"approve": False, "reason": "Escalate for human review"}
