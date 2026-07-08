"""Function tools shared by the agents."""

from __future__ import annotations

from agents import function_tool

from src.wrapper.guardrails import tool_guardrails

MAX_SUMMARY_LENGTH = 120

_tool_input_guardrails, _tool_output_guardrails = tool_guardrails()


def truncate_summary(text: str, max_length: int = MAX_SUMMARY_LENGTH) -> str:
    """Trim ``text`` to ``max_length`` characters, adding an ellipsis if needed.

    Kept separate from the decorated tool so the logic is directly testable.
    """
    trimmed = text.strip()
    if not trimmed:
        return "Empty input."
    if len(trimmed) <= max_length:
        return trimmed
    return f"{trimmed[: max_length - 3]}..."


@function_tool(
    tool_input_guardrails=_tool_input_guardrails,
    tool_output_guardrails=_tool_output_guardrails,
)
def summarize_text(text: str) -> str:
    """Summarize text into a single concise sentence.

    Args:
        text: The text to summarize.
    """
    return truncate_summary(text)
