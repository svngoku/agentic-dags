"""Prefect flow orchestrating the plan → execute → synthesize agent DAG.

Pipeline:
    1. The supervisor turns the request into a ``WorkPlan``.
    2. Steps are executed in parallel-group order; steps in the same group
       run concurrently (optionally capped by ``MAX_CONCURRENT_STEPS``).
       Before each group a token/cost budget is enforced, and groups that
       need approval pause the flow run for a typed approve/reject decision
       (unless ``AUTO_APPROVE`` is set).
    3. The writer synthesizes step results into a ``FinalOutput``.
    4. The report is persisted to MCP memory and run metrics are published
       as a Prefect artifact (both best effort).

Resilience & resumability:
    - Every model call is wrapped in ``run_with_resilience`` (retry with
      backoff on transient errors, optional ``LLM_FALLBACK_MODEL``).
    - Individual step failures are converted into
      ``StepResult(status="failed")`` instead of aborting the run, so the
      final report can call them out.
    - With ``CHECKPOINT_ENABLED``, successful steps are persisted and skipped
      on re-runs of the same work order; the checkpoint file is cleared after
      a fully successful run (every step ``ok``) when
      ``CHECKPOINT_CLEAR_ON_SUCCESS`` is set.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import AsyncExitStack

from agents import Agent, RunConfig, SQLiteSession
from agents.mcp import MCPServer
from prefect import flow, task
from prefect.cache_policies import NONE
from prefect.logging import get_run_logger

from src.agents.schemas import FinalOutput, PlanStep, StepResult, WorkPlan
from src.config import AgentBundle, AppConfig, build_agents, build_run_config
from src.mcp_servers import build_mcp_server
from src.wrapper.checkpoints import CheckpointStore
from src.wrapper.hitl import request_group_approval, skipped_results_for
from src.wrapper.memory import MEMORY_KEY, build_memory_agent
from src.wrapper.metrics import BudgetGuard, UsageTracker, publish_metrics_artifact
from src.wrapper.resilience import (
    fallback_model_from_env,
    gather_with_concurrency,
    max_concurrent_steps_from_env,
    run_with_resilience,
)
from src.wrapper.sessions import build_session, build_step_session

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_SECONDS = 3600
APPROVAL_POLL_SECONDS = 10
PLAN_MAX_TURNS = 10
EXECUTE_MAX_TURNS = 12
SYNTHESIZE_MAX_TURNS = 12
MEMORY_MAX_TURNS = 6

DEFAULT_REQUEST = "Create a weekly status report and open follow-ups for risks."


@flow
async def work_order_flow(request_text: str) -> FinalOutput:
    """Run the full plan → execute → synthesize DAG for ``request_text``."""
    config = AppConfig.from_env()
    config.validate_required()
    run_config = build_run_config(config)
    session = build_session(config.session_id, config.session_db_path)
    run_logger = get_run_logger()

    async with AsyncExitStack() as stack:
        mcp_servers: list[MCPServer] = []
        if not config.use_hosted_mcp:
            server = await stack.enter_async_context(build_mcp_server())
            run_logger.info("Connected MCP server: %s", server.name)
            mcp_servers = [server]

        agents = build_agents(config, mcp_servers=mcp_servers)
        return await _run_flow_body(
            request_text, agents, run_config, config, session, mcp_servers
        )


async def _run_flow_body(
    request_text: str,
    agents: AgentBundle,
    run_config: RunConfig,
    config: AppConfig,
    session: SQLiteSession | None,
    mcp_servers: list[MCPServer],
) -> FinalOutput:
    run_logger = get_run_logger()
    tracker = UsageTracker(config.model)
    budget = BudgetGuard.from_env()

    # ---- Step 1: Plan ----
    plan = await plan_task(request_text, agents.supervisor, run_config, session, tracker)
    run_logger.info("Plan: %s (%d steps)", plan.summary, len(plan.steps))

    # ---- Step 2: Fan-out execution by parallel group ----
    checkpoints = CheckpointStore.for_request(request_text)
    completed = checkpoints.load() if checkpoints else {}
    if completed:
        run_logger.info(
            "Loaded %d checkpointed step(s) from %s", len(completed), checkpoints.path
        )

    results: list[StepResult] = []
    for group_id in sorted({step.parallel_group for step in plan.steps}):
        group_steps = [step for step in plan.steps if step.parallel_group == group_id]

        # Resumability: only steps without a checkpointed "ok" result run.
        pending = [step for step in group_steps if step.id not in completed]
        if len(pending) < len(group_steps):
            run_logger.info(
                "Group %d: %d step(s) already checkpointed",
                group_id,
                len(group_steps) - len(pending),
            )
        if not pending:
            results.extend(completed[step.id] for step in group_steps)
            continue

        # Cost control: stop calling the model once the budget is spent.
        status = budget.status(tracker.metrics())
        if status.exceeded:
            run_logger.warning("Skipping group %d — %s", group_id, status.reason)
            fresh = skipped_results_for(pending, f"Budget exceeded: {status.reason}")
            results.extend(_merge_group_results(group_steps, completed, fresh))
            continue

        # Human-in-the-loop: typed approve/reject decision per group.
        if any(step.needs_approval for step in pending) and not config.auto_approve:
            run_logger.info("Pausing for approval on group %d", group_id)
            decision = await request_group_approval(
                group_id,
                pending,
                timeout=APPROVAL_TIMEOUT_SECONDS,
                poll_interval=APPROVAL_POLL_SECONDS,
            )
            if not decision.approved:
                reason = f"Rejected by operator: {decision.comment or 'no comment'}"
                run_logger.warning("Group %d rejected; skipping (%s)", group_id, reason)
                fresh = skipped_results_for(pending, reason)
                results.extend(_merge_group_results(group_steps, completed, fresh))
                continue
            run_logger.info("Approval received for group %d; resuming", group_id)

        executed = await execute_group(
            request_text,
            pending,
            agents.executor,
            run_config,
            config.session_id,
            config.session_db_path,
            tracker,
        )
        if checkpoints:
            checkpoints.save_many(executed)  # persists only status == "ok"
        results.extend(_merge_group_results(group_steps, completed, executed))

    failed_steps = [result.step_id for result in results if result.status == "failed"]
    if failed_steps:
        run_logger.warning(
            "%d of %d steps failed: %s", len(failed_steps), len(results), failed_steps
        )

    # ---- Step 3: Synthesis ----
    final_output = await synthesize_task(
        request_text, plan, results, agents.writer, run_config, session, tracker
    )

    # ---- Step 4: Persist summary to MCP memory (best effort) ----
    if mcp_servers:
        try:
            await save_latest_resume(
                final_output.report_markdown, config, run_config, mcp_servers, tracker
            )
        except Exception:
            run_logger.warning(
                "Failed to save the report to MCP memory; continuing", exc_info=True
            )

    # Clear checkpoints only when every step succeeded, so partial runs
    # (failed / skipped / rejected steps) can be resumed cheaply.
    if (
        checkpoints
        and checkpoints.clear_on_success
        and results
        and all(result.status == "ok" for result in results)
    ):
        checkpoints.clear()

    metrics = tracker.metrics()
    publish_metrics_artifact(metrics)
    cost = (
        f"${metrics.estimated_cost_usd:.4f}"
        if metrics.estimated_cost_usd is not None
        else "n/a"
    )
    run_logger.info(
        "Run usage: %d tokens across %d requests (est. cost %s)",
        metrics.total_tokens,
        metrics.requests,
        cost,
    )

    return final_output


def _merge_group_results(
    group_steps: list[PlanStep],
    completed: dict[str, StepResult],
    fresh: list[StepResult],
) -> list[StepResult]:
    """Order a group's results in plan order, preferring checkpointed ones.

    Every step must be covered by either ``completed`` or ``fresh``; a missing
    step is a programming error and raises ``KeyError``.
    """
    fresh_by_id = {result.step_id: result for result in fresh}
    return [completed.get(step.id) or fresh_by_id[step.id] for step in group_steps]


@task(retries=2, retry_delay_seconds=5, cache_policy=NONE)
async def plan_task(
    request_text: str,
    supervisor: Agent,
    run_config: RunConfig,
    session: SQLiteSession | None,
    tracker: UsageTracker,
) -> WorkPlan:
    """Ask the supervisor for a ``WorkPlan``; retry on empty plans."""
    result = await run_with_resilience(
        supervisor,
        request_text,
        max_turns=PLAN_MAX_TURNS,
        run_config=run_config,
        session=session,
        fallback_model=fallback_model_from_env(),
    )
    tracker.record("plan", result)
    plan = result.final_output_as(WorkPlan)
    if not plan.steps:
        raise ValueError("Supervisor returned a plan with no steps")
    return plan


@task(retries=2, retry_delay_seconds=5, cache_policy=NONE)
async def execute_group(
    request_text: str,
    group_steps: list[PlanStep],
    executor: Agent,
    run_config: RunConfig,
    session_id: str | None,
    session_db_path: str,
    tracker: UsageTracker,
) -> list[StepResult]:
    """Execute all steps of one parallel group concurrently.

    Fan-out is capped by ``MAX_CONCURRENT_STEPS`` (0 = unlimited). Failures
    are isolated per step: an exception in one step becomes a ``failed``
    ``StepResult`` rather than aborting its siblings.
    """
    coros = [
        _run_single_step(
            executor,
            request_text,
            step,
            run_config,
            session_id,
            session_db_path,
            tracker,
        )
        for step in group_steps
    ]
    outcomes = await gather_with_concurrency(
        max_concurrent_steps_from_env(),
        *coros,
        return_exceptions=True,
    )
    return [
        _coerce_step_result(step, outcome)
        for step, outcome in zip(group_steps, outcomes, strict=True)
    ]


def _coerce_step_result(
    step: PlanStep, outcome: StepResult | BaseException
) -> StepResult:
    """Map a step outcome to a ``StepResult``, converting errors to failures."""
    if isinstance(outcome, BaseException):
        if not isinstance(outcome, Exception):
            # Never swallow cancellation / system-exit signals.
            raise outcome
        logger.error("Step %s failed: %s", step.id, outcome, exc_info=outcome)
        return StepResult(
            step_id=step.id,
            status="failed",
            notes=[f"Execution error: {type(outcome).__name__}: {outcome}"],
        )
    return outcome


async def _run_single_step(
    executor: Agent,
    request_text: str,
    step: PlanStep,
    run_config: RunConfig,
    session_id: str | None,
    session_db_path: str,
    tracker: UsageTracker,
) -> StepResult:
    """Run the executor on one plan step in an isolated session."""
    prompt = (
        f"Original request:\n{request_text}\n\n"
        f"Execute this step. Use the suggested MCP tools first, then return "
        f"a StepResult JSON as your final message.\n\n"
        f"Step:\n{step.model_dump_json(indent=2)}"
    )
    step_session = build_step_session(session_id, step.id, session_db_path)
    result = await run_with_resilience(
        executor,
        prompt,
        max_turns=EXECUTE_MAX_TURNS,
        run_config=run_config,
        session=step_session,
        fallback_model=fallback_model_from_env(),
    )
    tracker.record(f"step:{step.id}", result)
    return result.final_output_as(StepResult)


@task(retries=2, retry_delay_seconds=5, cache_policy=NONE)
async def synthesize_task(
    request_text: str,
    plan: WorkPlan,
    results: list[StepResult],
    writer: Agent,
    run_config: RunConfig,
    session: SQLiteSession | None,
    tracker: UsageTracker,
) -> FinalOutput:
    """Ask the writer to synthesize step results into a ``FinalOutput``."""
    results_json = json.dumps([result.model_dump() for result in results], indent=2)
    prompt = (
        f"Request:\n{request_text}\n\n"
        f"Plan:\n{plan.model_dump_json(indent=2)}\n\n"
        f"Step results:\n{results_json}\n\n"
        f"Produce a FinalOutput JSON with report_markdown and actions_manifest."
    )
    result = await run_with_resilience(
        writer,
        prompt,
        max_turns=SYNTHESIZE_MAX_TURNS,
        run_config=run_config,
        session=session,
        fallback_model=fallback_model_from_env(),
    )
    tracker.record("synthesize", result)
    return result.final_output_as(FinalOutput)


@task(retries=2, retry_delay_seconds=5, cache_policy=NONE)
async def save_latest_resume(
    report_markdown: str,
    config: AppConfig,
    run_config: RunConfig,
    mcp_servers: list[MCPServer],
    tracker: UsageTracker,
) -> None:
    """Persist the final report under ``MEMORY_KEY`` via the memory agent."""
    memory_agent = build_memory_agent(config.model, mcp_servers)
    prompt = f"Save this summary as {MEMORY_KEY}:\n\n{report_markdown}\n"
    result = await run_with_resilience(
        memory_agent,
        prompt,
        max_turns=MEMORY_MAX_TURNS,
        run_config=run_config,
        fallback_model=fallback_model_from_env(),
    )
    tracker.record("memory", result)


if __name__ == "__main__":
    request = " ".join(sys.argv[1:]).strip() or DEFAULT_REQUEST
    asyncio.run(work_order_flow(request))
