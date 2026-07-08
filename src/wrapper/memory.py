"""Agent that persists run summaries via the MCP memory tools."""

from __future__ import annotations

from agents import Agent, ModelSettings
from agents.mcp import MCPServer

#: Key under which the latest work summary is stored in the MCP memory store.
MEMORY_KEY = "latest_work_resume"


def build_memory_agent(model: str, mcp_servers: list[MCPServer]) -> Agent:
    """Build an agent whose only job is to call ``save_memory``."""
    return Agent(
        name="MemoryWriter",
        instructions=(
            "You store a summary in memory using the MCP tool save_memory.\n"
            f"Always call save_memory with key '{MEMORY_KEY}' and the provided value.\n"
            "After the tool call, reply with a short confirmation."
        ),
        model=model,
        mcp_servers=mcp_servers,
        model_settings=ModelSettings(tool_choice="required", include_usage=True),
    )
