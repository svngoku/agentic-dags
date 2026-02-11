from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    id: str
    goal: str
    parallel_group: int = 0
    needs_approval: bool = False
    suggested_tools: List[str] = Field(default_factory=list)


class WorkPlan(BaseModel):
    summary: str
    steps: List[PlanStep]


class Artifact(BaseModel):
    key: str
    value: str


class StepResult(BaseModel):
    step_id: str
    status: Literal["ok", "failed", "skipped"]
    artifacts: List[Artifact] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class ActionEntry(BaseModel):
    tool: str
    input_summary: str
    output_summary: str


class ActionsManifest(BaseModel):
    actions: List[ActionEntry] = Field(default_factory=list)


class FinalOutput(BaseModel):
    report_markdown: str
    actions_manifest: ActionsManifest
