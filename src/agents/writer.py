"""Builder for the Writer (synthesis) agent."""

from __future__ import annotations

from agents import Agent, ModelSettings, Tool
from agents.mcp import MCPServer

from src.agents.schemas import FinalOutput


def build_writer(
    model: str,
    mcp_servers: list[MCPServer] | None = None,
    tools: list[Tool] | None = None,
) -> Agent:
    """Build the writer agent that synthesizes step results into a report."""
    return Agent(
        name="Writer",
        instructions=(
            "You are a report-writing agent.\n\n"
            "Given the original request, the plan, and the step results, produce a "
            "FinalOutput JSON with exactly these fields:\n"
            "- report_markdown: a well-formatted markdown report that addresses the "
            "original request. Include sections, summaries, and any risk call-outs.\n"
            "- actions_manifest: an object with an 'actions' list. Each entry has:\n"
            "  - tool: the MCP tool name that was used\n"
            "  - input_summary: short description of what was requested\n"
            "  - output_summary: short description of what was returned\n\n"
            "If no tools were used, set actions to an empty list.\n"
            "Return ONLY the FinalOutput JSON.\n"
        ),
        model=model,
        output_type=FinalOutput,
        mcp_servers=mcp_servers or [],
        tools=tools or [],
        model_settings=ModelSettings(include_usage=True),
    )
