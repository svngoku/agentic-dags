"""Unit tests for pure helpers in the work-order flow."""

from __future__ import annotations

import asyncio

import pytest

from src.agents.schemas import PlanStep, StepResult
from src.flows.work_order_flow import _coerce_step_result


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
