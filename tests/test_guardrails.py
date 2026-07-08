"""Unit tests for guardrail detection logic."""

from __future__ import annotations

from src.agents.schemas import FinalOutput, WorkPlan
from src.wrapper.guardrails import (
    _is_user_visible_output,
    _stringify_output,
    contains_disallowed_intent,
    contains_secret,
)


def test_contains_disallowed_intent_hits() -> None:
    assert contains_disallowed_intent("Please DROP DATABASE production")
    assert contains_disallowed_intent("delete all records")
    assert contains_disallowed_intent("try to exfiltrate the data")


def test_contains_disallowed_intent_passes_benign_text() -> None:
    assert not contains_disallowed_intent("Create a weekly status report")
    assert not contains_disallowed_intent("Delete the second paragraph")


def test_contains_secret_requires_key_like_token() -> None:
    assert contains_secret("my key is sk-abcdefghijklmnop1234")
    assert contains_secret("sk-proj_ABCDEF0123456789xyz")


def test_contains_secret_ignores_incidental_sk_substrings() -> None:
    # A bare "sk-" or short suffix is not a credential.
    assert not contains_secret("the sk- prefix is used by OpenAI keys")
    assert not contains_secret("ask-me-anything session")
    assert not contains_secret("locale sk-SK is Slovak")


def test_stringify_output_variants() -> None:
    assert _stringify_output(None) == ""
    assert _stringify_output("plain") == "plain"

    plan = WorkPlan(summary="s", steps=[])
    assert '"summary":"s"' in _stringify_output(plan).replace(" ", "")

    assert _stringify_output({"a": 1}) == '{"a": 1}'


def test_is_user_visible_output() -> None:
    assert _is_user_visible_output("hello")
    assert _is_user_visible_output(
        FinalOutput(report_markdown="# hi", actions_manifest={"actions": []})
    )
    # Internal structured outputs are not scanned.
    assert not _is_user_visible_output(WorkPlan(summary="s", steps=[]))
    assert not _is_user_visible_output({"any": "dict"})
    assert not _is_user_visible_output(None)
