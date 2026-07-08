"""Typed human-in-the-loop approvals for the work-order flow.

Upgrades the flow's blind ``pause_flow_run(key=...)`` gate into a typed
approve/reject decision built on Prefect's ``RunInput`` (verified against
Prefect 3.6: ``pause_flow_run(wait_for_input=..., timeout=..., poll_interval=...,
key=...)`` returns the submitted ``RunInput`` instance once the operator
resumes the run with input).

Building blocks:

* :class:`ApprovalDecision` -- the ``RunInput`` schema the operator fills in.
* :func:`approval_description` -- pure markdown builder describing the steps
  awaiting approval (unit-testable without a Prefect backend).
* :func:`request_group_approval` -- pauses the *current* flow run and waits
  for a decision; requires a live flow-run context.
* :func:`skipped_results_for` -- pure helper producing ``skipped``
  ``StepResult`` rows when an operator rejects a group.
"""

from __future__ import annotations

from prefect.flow_runs import pause_flow_run
from prefect.input import RunInput

from src.agents.schemas import PlanStep, StepResult


class ApprovalDecision(RunInput):
    """Operator decision for a paused approval gate.

    Operator flow: when the flow pauses, open the flow run in the Prefect UI,
    press **Resume**, and fill in the generated form (this model's fields,
    rendered beneath the markdown description of the pending steps). Submit
    with ``approved=True`` to run the group, or ``approved=False`` to skip
    every step in the group and let the flow continue; ``comment`` is echoed
    into the skipped steps' notes so the final report shows *why*.
    """

    approved: bool = False
    comment: str = ""


def approval_description(group_id: int, steps: list[PlanStep]) -> str:
    """Build the markdown shown in the Prefect UI resume form.

    Pure function (no flow-run context needed) so it can be unit tested; it
    lists each step's id, goal, approval flag, and suggested tools.
    """
    lines = [
        f"## Approval required: parallel group {group_id}",
        "",
        f"{len(steps)} step(s) are paused awaiting an operator decision:",
        "",
    ]
    for step in steps:
        marker = " **[needs approval]**" if step.needs_approval else ""
        lines.append(f"- `{step.id}` — {step.goal}{marker}")
        if step.suggested_tools:
            lines.append(f"  - suggested tools: {', '.join(step.suggested_tools)}")
    lines += [
        "",
        "Set **approved** to `true` to execute this group, or leave it `false`",
        "to skip these steps (add a **comment** to explain the rejection).",
    ]
    return "\n".join(lines)


async def request_group_approval(
    group_id: int,
    steps: list[PlanStep],
    *,
    timeout: int,
    poll_interval: int,
) -> ApprovalDecision:
    """Pause the current flow run until an operator submits a decision.

    Must be called from inside a running flow (``pause_flow_run`` raises
    otherwise). The pause ``key`` mirrors the legacy gate
    (``approval-group-{group_id}``) so each group pauses at most once.
    Resuming without input fails the flow run (Prefect semantics), so a
    non-``None`` decision is always returned on the happy path.
    """
    decision = await pause_flow_run(
        wait_for_input=ApprovalDecision.with_initial_data(
            description=approval_description(group_id, steps),
        ),
        timeout=timeout,
        poll_interval=poll_interval,
        key=f"approval-group-{group_id}",
    )
    if decision is None:  # pragma: no cover - defensive; see docstring
        raise RuntimeError(
            f"Flow run resumed without an ApprovalDecision for group {group_id}"
        )
    return decision


def skipped_results_for(steps: list[PlanStep], reason: str) -> list[StepResult]:
    """Return one ``skipped`` :class:`StepResult` per step, carrying ``reason``.

    Used when an operator rejects an approval group: the flow records the
    steps as skipped (with the operator's reason in ``notes``) and continues,
    so the writer can call them out in the final report.
    """
    return [
        StepResult(step_id=step.id, status="skipped", notes=[reason])
        for step in steps
    ]
