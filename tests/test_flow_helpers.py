"""Unit tests for pure helpers in the work-order flow."""

from __future__ import annotations

import asyncio

import pytest

from src.agents.schemas import PlanStep, StepResult
from src.flows.work_order_flow import _coerce_step_result, _merge_group_results


def _step(step_id: str = "step-1") -> PlanStep:
    return PlanStep(id=step_id, goal="do something")


def test_successful_result_passes_through() -> None:
    result = StepResult(step_id="step-1", status="ok")
    assert _coerce_step_result(_step(), result) is result


def test_exception_becomes_failed_result() -> None:
    error = RuntimeError("model exploded")
    result = _coerce_step_result(_step("boom"), error)

    assert result.status == "failed"
    assert result.step_id == "boom"
    assert any("model exploded" in note for note in result.notes)
    assert any("RuntimeError" in note for note in result.notes)


def test_cancellation_is_never_swallowed() -> None:
    with pytest.raises(asyncio.CancelledError):
        _coerce_step_result(_step(), asyncio.CancelledError())


def _ok(step_id: str) -> StepResult:
    return StepResult(step_id=step_id, status="ok")


def test_merge_group_results_preserves_plan_order() -> None:
    steps = [_step("a"), _step("b"), _step("c")]
    merged = _merge_group_results(steps, {}, [_ok("c"), _ok("a"), _ok("b")])
    assert [result.step_id for result in merged] == ["a", "b", "c"]


def test_merge_group_results_prefers_checkpointed() -> None:
    steps = [_step("a"), _step("b")]
    checkpointed = StepResult(step_id="a", status="ok", notes=["from checkpoint"])
    merged = _merge_group_results(steps, {"a": checkpointed}, [_ok("b")])
    assert merged[0] is checkpointed
    assert merged[1].step_id == "b"


def test_merge_group_results_missing_step_raises() -> None:
    steps = [_step("a"), _step("b")]
    with pytest.raises(KeyError):
        _merge_group_results(steps, {}, [_ok("a")])
