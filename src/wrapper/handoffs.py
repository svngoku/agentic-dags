"""Helpers for building agent handoffs."""

from __future__ import annotations

from agents import Agent, Handoff, RunContextWrapper, handoff
from agents.extensions import handoff_filters


def build_handoff(target: Agent) -> Handoff:
    """Build a handoff to ``target`` with tool-call history stripped."""
    return handoff(
        agent=target,
        tool_name_override=f"transfer_to_{target.name.lower()}",
        tool_description_override=f"Handoff to {target.name} for specialized help.",
        input_filter=handoff_filters.remove_all_tools,
        on_handoff=_on_handoff,
    )


def _on_handoff(ctx: RunContextWrapper[None]) -> None:
    """Hook for logging/metrics; intentionally a no-op by default."""
    return None
