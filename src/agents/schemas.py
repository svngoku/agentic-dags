"""Pydantic schemas defining the I/O contract between the agents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    """A single unit of work produced by the supervisor."""

    id: str
    goal: str
    parallel_group: int = 0
    needs_approval: bool = False
    suggested_tools: list[str] = Field(default_factory=list)


class WorkPlan(BaseModel):
    """The supervisor's structured plan for a request."""

    summary: str
    steps: list[PlanStep]


class Artifact(BaseModel):
    """A key/value artifact produced while executing a step."""

    key: str
    value: str


class StepResult(BaseModel):
    """The executor's outcome for one plan step."""

    step_id: str
    status: Literal["ok", "failed", "skipped"]
    artifacts: list[Artifact] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ActionEntry(BaseModel):
    """One tool invocation recorded in the actions manifest."""

    tool: str
    input_summary: str
    output_summary: str


class ActionsManifest(BaseModel):
    """Audit trail of every tool used during the run."""

    actions: list[ActionEntry] = Field(default_factory=list)


class FinalOutput(BaseModel):
    """The writer's final deliverable: a report plus an audit manifest."""

    report_markdown: str
    actions_manifest: ActionsManifest
