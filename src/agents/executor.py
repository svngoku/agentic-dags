from __future__ import annotations

from typing import List

from agents import Agent, ModelSettings
from agents.mcp import MCPServer

from src.agents.schemas import StepResult


def build_executor(
    model: str, mcp_servers: List[MCPServer] | None = None, tools: list | None = None
) -> Agent:
    """Build the executor agent."""
    return Agent(
        name="Executor",
        instructions=(
            "You execute a single plan step using MCP tools.\n\n"
            "Phase 1 — Tool calls:\n"
            "  Call the MCP tools listed in suggested_tools to gather data or "
            "perform actions. You may call multiple tools sequentially.\n\n"
            "Phase 2 — Final answer:\n"
            "  After all tool calls complete, return a StepResult JSON with:\n"
            "  - step_id: copy the 'id' field from the step\n"
            '  - status: "ok" if tools succeeded, "failed" if they errored, '
            '"skipped" if not applicable\n'
            "  - artifacts: list of {key, value} pairs summarizing outputs\n"
            "  - notes: list of string observations or warnings\n\n"
            "CRITICAL: Your final message MUST be a StepResult JSON object. "
            "Never return raw tool arguments or tool outputs as your final answer.\n"
        ),
        model=model,
        output_type=StepResult,
        mcp_servers=mcp_servers or [],
        tools=tools or [],
        model_settings=ModelSettings(
            tool_choice="auto",
            parallel_tool_calls=False,
            include_usage=True,
        ),
    )
