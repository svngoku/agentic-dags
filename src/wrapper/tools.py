from __future__ import annotations

from agents import function_tool

from src.wrapper.guardrails import tool_guardrails


tool_input_guardrails, tool_output_guardrails = tool_guardrails()


@function_tool(
    tool_input_guardrails=tool_input_guardrails,
    tool_output_guardrails=tool_output_guardrails,
)
def summarize_text(text: str) -> str:
    """Summarize text into a single concise sentence.

    Args:
        text: The text to summarize.
    """
    if not text.strip():
        return "Empty input."
    trimmed = text.strip()
    if len(trimmed) <= 120:
        return trimmed
    return f"{trimmed[:117]}..."
