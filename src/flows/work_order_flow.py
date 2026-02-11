from __future__ import annotations

import asyncio
import logging

from prefect import flow, task
from prefect.cache_policies import NONE
from prefect.flow_runs import pause_flow_run

from agents import Runner

from src.config import AppConfig, build_agents, build_run_config
from src.mcp_servers import build_mcp_server
from src.agents.schemas import FinalOutput, StepResult, WorkPlan
from src.wrapper.sessions import build_session, build_step_session
from src.wrapper.memory import MEMORY_KEY, build_memory_agent

logger = logging.getLogger(__name__)


@flow
async def work_order_flow(request_text: str) -> FinalOutput:
    config = AppConfig.from_env()
    config.validate_required()
    run_config = build_run_config(config)

    session = build_session(config.session_id, config.session_db_path)

    if config.use_hosted_mcp:
        supervisor, executor, writer = build_agents(config, mcp_servers=[])
        output = await _run_flow_body(
            request_text,
            supervisor,
            executor,
            writer,
            run_config,
            config,
            session,
            mcp_servers=[],
        )
        return output

    server = build_mcp_server()
    async with server:
        logger.info("Connected MCP server: %s", server.name)
        mcp_servers = [server]

        supervisor, executor, writer = build_agents(config, mcp_servers=mcp_servers)
        output = await _run_flow_body(
            request_text,
            supervisor,
            executor,
            writer,
            run_config,
            config,
            session,
            mcp_servers=mcp_servers,
        )
        return output


async def _run_flow_body(
    request_text: str,
    supervisor,
    executor,
    writer,
    run_config,
    config: AppConfig,
    session,
    mcp_servers,
) -> FinalOutput:
    # ---- Step 1: Plan ----
    plan_out = await plan_task(request_text, supervisor, run_config, session)
    logger.info("Plan: %s (%d steps)", plan_out.summary, len(plan_out.steps))

    # ---- Step 2: Fan-out execution by parallel group ----
    results: list[StepResult] = []

    for group_id in sorted({s.parallel_group for s in plan_out.steps}):
        group_steps = [s for s in plan_out.steps if s.parallel_group == group_id]

        if any(s.needs_approval for s in group_steps) and not config.auto_approve:
            logger.info("Pausing for approval on group %d", group_id)
            await pause_flow_run(
                timeout=3600,
                poll_interval=10,
                key=f"approval-group-{group_id}",
            )

        group_results = await execute_group(
            request_text,
            group_steps,
            executor,
            run_config,
            config.session_id,
            config.session_db_path,
        )
        results.extend(group_results)

    # ---- Step 3: Synthesis ----
    final_output = await synthesize_task(
        request_text, plan_out, results, writer, run_config, session
    )

    # ---- Step 4: Save latest resume to memory MCP ----
    if mcp_servers:
        await save_latest_resume(
            final_output.report_markdown,
            config,
            run_config,
            mcp_servers,
        )

    return final_output


@task(retries=2, retry_delay_seconds=5, cache_policy=NONE)
async def plan_task(
    request_text: str, supervisor, run_config, session
) -> WorkPlan:
    result = await Runner.run(
        supervisor,
        request_text,
        max_turns=10,
        run_config=run_config,
        session=session,
    )
    return result.final_output


@task(retries=2, retry_delay_seconds=5, cache_policy=NONE)
async def execute_group(
    request_text: str,
    group_steps: list,
    executor,
    run_config,
    session_id: str | None,
    session_db_path: str,
) -> list[StepResult]:
    coros = [
        _run_single_step(
            executor,
            request_text,
            step,
            run_config,
            session_id,
            session_db_path,
        )
        for step in group_steps
    ]
    return list(await asyncio.gather(*coros))


async def _run_single_step(
    executor,
    request_text: str,
    step,
    run_config,
    session_id: str | None,
    session_db_path: str,
) -> StepResult:
    prompt = (
        f"Original request:\n{request_text}\n\n"
        f"Execute this step. Use the suggested MCP tools first, then return "
        f"a StepResult JSON as your final message.\n\n"
        f"Step:\n{step.model_dump_json(indent=2)}"
    )
    step_session = build_step_session(session_id, step.id, session_db_path)
    result = await Runner.run(
        executor,
        prompt,
        max_turns=12,
        run_config=run_config,
        session=step_session,
    )
    return result.final_output


@task(retries=2, retry_delay_seconds=5, cache_policy=NONE)
async def synthesize_task(
    request_text: str,
    plan_out: WorkPlan,
    results: list[StepResult],
    writer,
    run_config,
    session,
) -> FinalOutput:
    prompt = (
        f"Request:\n{request_text}\n\n"
        f"Plan:\n{plan_out.model_dump_json(indent=2)}\n\n"
        f"Step results:\n{[r.model_dump() for r in results]}\n\n"
        f"Produce a FinalOutput JSON with report_markdown and actions_manifest."
    )
    result = await Runner.run(
        writer,
        prompt,
        max_turns=12,
        run_config=run_config,
        session=session,
    )
    return result.final_output


@task(retries=2, retry_delay_seconds=5, cache_policy=NONE)
async def save_latest_resume(
    report_markdown: str,
    config: AppConfig,
    run_config,
    mcp_servers,
) -> None:
    memory_agent = build_memory_agent(config.model, mcp_servers)
    prompt = (
        f"Save this summary as {MEMORY_KEY}:\n\n{report_markdown}\n"
    )
    await Runner.run(
        memory_agent,
        prompt,
        max_turns=6,
        run_config=run_config,
    )


if __name__ == "__main__":
    asyncio.run(
        work_order_flow(
            "Create a weekly status report and open follow-ups for risks."
        )
    )
