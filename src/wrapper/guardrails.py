from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from agents import (
    GuardrailFunctionOutput,
    RunContextWrapper,
    TResponseInputItem,
    input_guardrail,
    output_guardrail,
    tool_input_guardrail,
    tool_output_guardrail,
    ToolGuardrailFunctionOutput,
)


DISALLOWED_PATTERNS = [
    "exfiltrate",
    "delete all",
    "drop database",
]


@input_guardrail
async def block_disallowed_intents(
    ctx: RunContextWrapper[None],
    agent,
    input: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    text = input if isinstance(input, str) else json.dumps(input)
    hit = any(pat in text.lower() for pat in DISALLOWED_PATTERNS)
    return GuardrailFunctionOutput(
        output_info={"blocked": hit, "patterns": DISALLOWED_PATTERNS if hit else []},
        tripwire_triggered=hit,
    )


def _stringify_output(output: Any) -> str:
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
    if isinstance(output, str):
        return True
    if isinstance(output, BaseModel):
        # Only enforce on outputs that are meant for users.
        return hasattr(output, "report_markdown") or hasattr(output, "response")
    return False


@output_guardrail
async def block_sensitive_output(
    ctx: RunContextWrapper[None],
    agent,
    output: Any,
) -> GuardrailFunctionOutput:
    if not _is_user_visible_output(output):
        return GuardrailFunctionOutput(output_info={"blocked": False}, tripwire_triggered=False)
    text = _stringify_output(output)
    hit = "sk-" in text
    return GuardrailFunctionOutput(
        output_info={"blocked": hit},
        tripwire_triggered=hit,
    )


@tool_input_guardrail
def block_tool_secrets(data) -> ToolGuardrailFunctionOutput:
    args = json.loads(data.context.tool_arguments or "{}")
    if "sk-" in json.dumps(args):
        return ToolGuardrailFunctionOutput.reject_content(
            "Remove secrets before calling this tool."
        )
    return ToolGuardrailFunctionOutput.allow()


@tool_output_guardrail
def redact_tool_output(data) -> ToolGuardrailFunctionOutput:
    text = str(data.output or "")
    if "sk-" in text:
        return ToolGuardrailFunctionOutput.reject_content(
            "Output contained sensitive data."
        )
    return ToolGuardrailFunctionOutput.allow()


def build_input_guardrails():
    return [block_disallowed_intents]


def build_output_guardrails():
    return [block_sensitive_output]


def tool_guardrails():
    return [block_tool_secrets], [redact_tool_output]
