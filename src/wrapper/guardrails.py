"""Input, output and tool guardrails for the agent runs.

Pure detection helpers (``contains_disallowed_intent``, ``contains_secret``)
are kept separate from the SDK-decorated guardrail functions so they can be
unit-tested without a run context.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agents import (
    Agent,
    GuardrailFunctionOutput,
    RunContextWrapper,
    ToolGuardrailFunctionOutput,
    ToolInputGuardrailData,
    ToolOutputGuardrailData,
    TResponseInputItem,
    input_guardrail,
    output_guardrail,
    tool_input_guardrail,
    tool_output_guardrail,
)
from pydantic import BaseModel

#: Phrases that indicate a clearly disallowed intent.
DISALLOWED_PATTERNS: tuple[str, ...] = (
    "exfiltrate",
    "delete all",
    "drop database",
)

#: Matches likely secret keys (e.g. OpenAI-style ``sk-...`` tokens). Requiring a
#: minimum key length avoids false positives on incidental "sk-" substrings.
SECRET_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")


def contains_disallowed_intent(text: str) -> bool:
    """Return True when ``text`` matches any disallowed pattern."""
    lowered = text.lower()
    return any(pattern in lowered for pattern in DISALLOWED_PATTERNS)


def contains_secret(text: str) -> bool:
    """Return True when ``text`` appears to contain a secret key."""
    return bool(SECRET_PATTERN.search(text))


@input_guardrail
async def block_disallowed_intents(
    ctx: RunContextWrapper[None],
    agent: Agent,
    input: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    """Trip when the user input contains a clearly disallowed intent."""
    text = input if isinstance(input, str) else json.dumps(input)
    hit = contains_disallowed_intent(text)
    return GuardrailFunctionOutput(
        output_info={"blocked": hit, "patterns": list(DISALLOWED_PATTERNS) if hit else []},
        tripwire_triggered=hit,
    )


def _stringify_output(output: Any) -> str:
    """Best-effort conversion of an agent output to a scannable string."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, BaseModel):
        try:
            return output.model_dump_json()
        except Exception:
            return str(output)
    try:
        return json.dumps(output)
    except Exception:
        return str(output)


def _is_user_visible_output(output: Any) -> bool:
    """Only enforce the output guardrail on outputs that are meant for users."""
    if isinstance(output, str):
        return True
    if isinstance(output, BaseModel):
        return hasattr(output, "report_markdown") or hasattr(output, "response")
    return False


@output_guardrail
async def block_sensitive_output(
    ctx: RunContextWrapper[None],
    agent: Agent,
    output: Any,
) -> GuardrailFunctionOutput:
    """Trip when a user-visible output appears to leak a secret key."""
    if not _is_user_visible_output(output):
        return GuardrailFunctionOutput(output_info={"blocked": False}, tripwire_triggered=False)
    hit = contains_secret(_stringify_output(output))
    return GuardrailFunctionOutput(
        output_info={"blocked": hit},
        tripwire_triggered=hit,
    )


@tool_input_guardrail
def block_tool_secrets(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
    """Reject tool calls whose arguments appear to contain secrets.

    Scans the raw argument payload so malformed JSON cannot crash the
    guardrail itself.
    """
    raw_args = data.context.tool_arguments or ""
    if contains_secret(raw_args):
        return ToolGuardrailFunctionOutput.reject_content(
            "Remove secrets before calling this tool."
        )
    return ToolGuardrailFunctionOutput.allow()


@tool_output_guardrail
def redact_tool_output(data: ToolOutputGuardrailData) -> ToolGuardrailFunctionOutput:
    """Reject tool outputs that appear to contain secrets."""
    text = str(data.output or "")
    if contains_secret(text):
        return ToolGuardrailFunctionOutput.reject_content(
            "Output contained sensitive data."
        )
    return ToolGuardrailFunctionOutput.allow()


def build_input_guardrails() -> list:
    """Input guardrails applied to every run via ``RunConfig``."""
    return [block_disallowed_intents]


def build_output_guardrails() -> list:
    """Output guardrails applied to every run via ``RunConfig``."""
    return [block_sensitive_output]


def tool_guardrails() -> tuple[list, list]:
    """Return ``(tool_input_guardrails, tool_output_guardrails)`` for function tools."""
    return [block_tool_secrets], [redact_tool_output]
