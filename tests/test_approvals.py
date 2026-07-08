"""Unit tests for the hosted MCP approval policy."""

from __future__ import annotations

from types import SimpleNamespace

from src.wrapper.approvals import SAFE_TOOLS, approve_tool


def _request_for(tool_name: str) -> SimpleNamespace:
    return SimpleNamespace(data=SimpleNamespace(name=tool_name))


def test_safe_tools_are_auto_approved() -> None:
    for tool_name in SAFE_TOOLS:
        result = approve_tool(_request_for(tool_name))  # type: ignore[arg-type]
        assert result == {"approve": True}


def test_unsafe_tools_are_escalated() -> None:
    result = approve_tool(_request_for("update_ticket"))  # type: ignore[arg-type]
    assert result["approve"] is False
    assert "reason" in result
