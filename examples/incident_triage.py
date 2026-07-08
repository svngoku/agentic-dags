"""Incident-triage example: adapting the work-order template to a new domain.

This is the template's "copy me" example. It reuses the stock supervisor /
executor / writer trio unchanged and only swaps the *request* — which is
often all a new use case needs. To go further, builders typically change:

- **Instructions** — edit the builders in ``src/agents/{supervisor,executor,
  writer}.py`` to speak your domain (incidents, orders, campaigns, ...).
- **Tools** — replace the canned tools in ``src/mcp_stub_server.py`` with a
  real MCP server (PagerDuty, Jira, Grafana, ...) and point ``MCP_URL`` at
  it. Keep tool docstrings sharp: the LLM reads them.
- **Schemas** — extend ``src/agents/schemas.py`` (e.g. add ``severity`` to
  ``PlanStep`` or an ``IncidentReport`` final output) so the contract between
  agents matches your domain.
- **Approval / parallelism flags** — the supervisor decides ``needs_approval``
  and ``parallel_group`` per step; tune its instructions (or force
  ``AUTO_APPROVE=0``) to gate risky actions behind a human.

Two ways to run it (both need the MCP stub up and ``OPENAI_API_KEY`` set —
required at runtime only, importing this module is offline-safe):

    uv run python -m src.mcp_stub_server            # terminal 1
    uv run python -m examples.incident_triage       # direct SDK path
    uv run python -m examples.incident_triage --flow  # full Prefect DAG

The default (direct SDK) path shows the pieces the Prefect flow is built
from: an :class:`~src.config.AppConfig` constructed in code (with a model
override), ``build_agents`` for the trio, and ``src.wrapper.runner.run_agent``
to plan and to execute one step. ``--flow`` runs the same request through
``work_order_flow`` with retries, parallel groups, and approval pauses.
"""

from __future__ import annotations

import asyncio
import os
import sys

from src.agents.schemas import StepResult, WorkPlan
from src.config import AppConfig, build_agents, build_run_config
from src.flows.work_order_flow import work_order_flow
from src.mcp_servers import build_mcp_server
from src.wrapper.runner import run_agent

#: Default MCP endpoint (the local stub). Point this at your real server.
STUB_MCP_URL = "http://localhost:8000/mcp"

PLAN_MAX_TURNS = 10
STEP_MAX_TURNS = 12


def build_request() -> str:
    """Return the incident-triage request handed to the supervisor.

    Pure helper (no I/O) so tests — and your own code — can reuse it.
    Mentioning the stub's tool names nudges the planner toward them.
    """
    return (
        "Triage the P1 incident INC-4021 (checkout latency spike): "
        "gather this week's operational metrics with read_metrics, "
        "search runbooks and past incident docs with search_docs, "
        "open follow-up tasks with create_task for remediation and postmortem, "
        "update ticket INC-4021 with the current status, "
        "and produce an incident report with a timeline, impact summary, "
        "and prioritized action items."
    )


def build_config() -> AppConfig:
    """Build an :class:`AppConfig` in code instead of ``AppConfig.from_env``.

    This is where builders override the model, workflow name, or approval
    behavior programmatically. Only the API key still comes from the
    environment (read at call time, never at import time).
    """
    return AppConfig(
        model=os.getenv("INCIDENT_MODEL", "gpt-4.1"),  # model override knob
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        workflow_name="Incident Triage",
        auto_approve=True,  # demo: don't pause; set False to gate risky steps
    )


async def run_direct() -> None:
    """Plan the incident and execute the first step via the SDK wrapper.

    Demonstrates the building blocks without Prefect: connect the MCP
    server, build the agent trio, run the supervisor for a ``WorkPlan``,
    then run the executor on the first step for a ``StepResult``.
    """
    config = build_config()
    config.validate_required()
    run_config = build_run_config(config)

    async with build_mcp_server() as server:
        agents = build_agents(config, mcp_servers=[server])

        plan_run = await run_agent(
            agents.supervisor,
            build_request(),
            run_config=run_config,
            max_turns=PLAN_MAX_TURNS,
        )
        plan = plan_run.final_output_as(WorkPlan)
        print(f"Plan: {plan.summary}")
        for step in plan.steps:
            flags = f"group={step.parallel_group} approval={step.needs_approval}"
            print(f"  [{step.id}] {step.goal} ({flags})")

        first_step = plan.steps[0]
        step_run = await run_agent(
            agents.executor,
            (
                f"Original request:\n{build_request()}\n\n"
                f"Execute this step and return a StepResult JSON.\n\n"
                f"Step:\n{first_step.model_dump_json(indent=2)}"
            ),
            run_config=run_config,
            max_turns=STEP_MAX_TURNS,
        )
        result = step_run.final_output_as(StepResult)
        print(f"\nFirst step result: {result.model_dump_json(indent=2)}")


async def run_flow() -> None:
    """Run the full plan -> execute -> synthesize DAG for the incident."""
    final = await work_order_flow(build_request())
    print(final.report_markdown)


async def main(use_flow: bool = False) -> None:
    """Entry point: direct SDK path by default, full Prefect DAG with ``--flow``."""
    os.environ.setdefault("MCP_URL", STUB_MCP_URL)
    if use_flow:
        await run_flow()
    else:
        await run_direct()


if __name__ == "__main__":
    asyncio.run(main(use_flow="--flow" in sys.argv[1:]))
