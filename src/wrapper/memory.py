from __future__ import annotations

from agents import Agent, ModelSettings
from agents.mcp import MCPServer


MEMORY_KEY = "latest_work_resume"


def build_memory_agent(model: str, mcp_servers: list[MCPServer]) -> Agent:
    return Agent(
        name="MemoryWriter",
        instructions=(
            "You store a summary in memory using the MCP tool save_memory.\n"
            "Always call save_memory with key 'latest_work_resume' and the provided value.\n"
            "After the tool call, reply with a short confirmation."
        ),
        model=model,
        mcp_servers=mcp_servers,
        model_settings=ModelSettings(tool_choice="required", include_usage=True),
    )
