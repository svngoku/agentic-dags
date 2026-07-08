"""Unit tests for the typed human-in-the-loop approval helpers."""

from __future__ import annotations

import pydantic

from src.agents.schemas import PlanStep, StepResult
from src.wrapper.hitl import (
    ApprovalDecision,
    approval_description,
    skipped_results_for,
)


def _steps() -> list[PlanStep]:
    return [
        PlanStep(
            id="step-1",
            goal="Update the production ticket",
            parallel_group=2,
            needs_approval=True,
            suggested_tools=["update_ticket", "create_task"],
        ),
        PlanStep(id="step-2", goal="Summarize weekly metrics", parallel_group=2),
    ]


# ---- ApprovalDecision ------------------------------------------------------


def test_approval_decision_defaults_to_rejected() -> None:
    decision = ApprovalDecision()
    assert decision.approved is False
    assert decision.comment == ""


def test_approval_decision_is_a_pydantic_model() -> None:
    assert issubclass(ApprovalDecision, pydantic.BaseModel)
    assert set(ApprovalDecision.model_fields) >= {"approved", "comment"}


def test_approval_decision_json_round_trip() -> None:
    decision = ApprovalDecision(approved=True, comment="ship it")
    restored = ApprovalDecision.model_validate_json(decision.model_dump_json())
    assert restored.approved is True
    assert restored.comment == "ship it"


def test_with_initial_data_builds_described_subclass() -> None:
    """The Prefect API `request_group_approval` relies on must exist."""
    subclass = ApprovalDecision.with_initial_data(description="## gate", approved=False)
    assert issubclass(subclass, ApprovalDecision)
    instance = subclass(approved=True)
    assert isinstance(instance, ApprovalDecision)
    assert instance.comment == ""


# ---- approval_description ---------------------------------------------------


def test_description_mentions_group_steps_and_goals() -> None:
    text = approval_description(2, _steps())
    assert "group 2" in text
    assert "step-1" in text and "step-2" in text
    assert "Update the production ticket" in text
    assert "Summarize weekly metrics" in text


def test_description_marks_approval_steps_and_tools() -> None:
    text = approval_description(2, _steps())
    assert "[needs approval]" in text
    assert "update_ticket, create_task" in text
    # Operator instructions reference the ApprovalDecision fields.
    assert "**approved**" in text
    assert "comment" in text


def test_description_marker_only_on_flagged_steps() -> None:
    text = approval_description(2, _steps())
    flagged = [line for line in text.splitlines() if "[needs approval]" in line]
    assert len(flagged) == 1
    assert "step-1" in flagged[0]


# ---- skipped_results_for -----------------------------------------------------


def test_skipped_results_cover_every_step_with_reason() -> None:
    reason = "Rejected by operator: too risky"
    results = skipped_results_for(_steps(), reason)

    assert [result.step_id for result in results] == ["step-1", "step-2"]
    assert all(result.status == "skipped" for result in results)
    assert all(reason in result.notes for result in results)


def test_skipped_results_are_valid_step_results() -> None:
    for result in skipped_results_for(_steps(), "nope"):
        restored = StepResult.model_validate(result.model_dump())
        assert restored == result
        assert restored.artifacts == []


def test_skipped_results_empty_steps() -> None:
    assert skipped_results_for([], "unused") == []
