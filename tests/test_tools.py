"""Unit tests for function tools."""

from __future__ import annotations

from src.wrapper.tools import MAX_SUMMARY_LENGTH, summarize_text, truncate_summary


def test_truncate_summary_empty_input() -> None:
    assert truncate_summary("") == "Empty input."
    assert truncate_summary("   \n\t ") == "Empty input."


def test_truncate_summary_short_text_passthrough() -> None:
    assert truncate_summary("Ship v2 next week.") == "Ship v2 next week."
    assert truncate_summary("  padded  ") == "padded"


def test_truncate_summary_long_text_is_trimmed() -> None:
    long_text = "word " * 100
    result = truncate_summary(long_text)
    assert len(result) == MAX_SUMMARY_LENGTH
    assert result.endswith("...")


def test_truncate_summary_respects_custom_length() -> None:
    result = truncate_summary("abcdefghij", max_length=5)
    assert result == "ab..."
    assert len(result) == 5


def test_summarize_text_is_a_function_tool() -> None:
    # The decorator wraps the function into an SDK FunctionTool.
    assert summarize_text.name == "summarize_text"
    assert "Summarize text" in (summarize_text.description or "")
