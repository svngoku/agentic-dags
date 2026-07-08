"""Builder for the Supervisor (planning) agent."""

from __future__ import annotations

from agents import Agent, ModelSettings, Tool
from agents.mcp import MCPServer

from src.agents.schemas import WorkPlan


def build_supervisor(
    model: str,
    mcp_servers: list[MCPServer] | None = None,
    tools: list[Tool] | None = None,
) -> Agent:
    """Build the Supervisor (planning) agent.

    The supervisor's ONLY job is to produce a WorkPlan. It has no handoffs —
    the Prefect flow handles orchestration of executor / writer agents.
    """
    return Agent(
        name="Supervisor",
        instructions=(
            "You are a planning agent. Given a user request, produce a "
            "structured WorkPlan JSON.\n\n"
            "Rules:\n"
            "- summary: one-line description of the overall plan.\n"
            "- steps: list of PlanStep objects.\n"
            "  - id: short unique slug (e.g. 'fetch-metrics').\n"
            "  - goal: what this step achieves.\n"
            "  - parallel_group: int — steps in the same group run in parallel.\n"
            "  - needs_approval: true only for destructive / costly actions.\n"
            "  - suggested_tools: list of MCP tool names to use (from: "
            "search_docs, read_metrics, create_task, update_ticket).\n\n"
            "Return ONLY the WorkPlan JSON. Do NOT call any tools yourself.\n"
        ),
        model=model,
        output_type=WorkPlan,
        mcp_servers=mcp_servers or [],
        tools=tools or [],
        model_settings=ModelSettings(include_usage=True),
    )
