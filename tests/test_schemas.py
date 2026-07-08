"""Unit tests for the agent I/O schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.schemas import (
    ActionEntry,
    ActionsManifest,
    Artifact,
    FinalOutput,
    PlanStep,
    StepResult,
    WorkPlan,
)


def test_plan_step_defaults() -> None:
    step = PlanStep(id="fetch-metrics", goal="Fetch weekly metrics")
    assert step.parallel_group == 0
    assert step.needs_approval is False
    assert step.suggested_tools == []


def test_work_plan_round_trip() -> None:
    plan = WorkPlan(
        summary="Weekly report",
        steps=[
            PlanStep(id="a", goal="A", parallel_group=0),
            PlanStep(id="b", goal="B", parallel_group=1, needs_approval=True),
        ],
    )
    restored = WorkPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan


def test_step_result_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        StepResult(step_id="a", status="exploded")


def test_step_result_accepts_valid_statuses() -> None:
    for status in ("ok", "failed", "skipped"):
        result = StepResult(step_id="a", status=status)
        assert result.status == status
        assert result.artifacts == []
        assert result.notes == []


def test_final_output_round_trip() -> None:
    output = FinalOutput(
        report_markdown="# Report",
        actions_manifest=ActionsManifest(
            actions=[
                ActionEntry(
                    tool="read_metrics",
                    input_summary="week 27",
                    output_summary="uptime 99.95%",
                )
            ]
        ),
    )
    restored = FinalOutput.model_validate_json(output.model_dump_json())
    assert restored == output
    assert restored.actions_manifest.actions[0].tool == "read_metrics"


def test_artifact_fields() -> None:
    artifact = Artifact(key="metrics", value="uptime 99.95%")
    assert artifact.key == "metrics"
    assert artifact.value == "uptime 99.95%"
