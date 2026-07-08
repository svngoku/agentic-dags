"""Unit tests for session builders."""

from __future__ import annotations

from pathlib import Path

from agents import SQLiteSession

from src.wrapper.sessions import build_session, build_step_session


def test_build_session_disabled_without_id(tmp_path: Path) -> None:
    assert build_session(None, str(tmp_path / "s.db")) is None
    assert build_session("", str(tmp_path / "s.db")) is None


def test_build_session_with_id(tmp_path: Path) -> None:
    session = build_session("main", str(tmp_path / "s.db"))
    assert isinstance(session, SQLiteSession)
    assert session.session_id == "main"


def test_build_step_session_isolates_by_step(tmp_path: Path) -> None:
    db_path = str(tmp_path / "s.db")
    first = build_step_session("main", "step-1", db_path)
    second = build_step_session("main", "step-2", db_path)

    assert first is not None and second is not None
    assert first.session_id == "main:step-1"
    assert second.session_id == "main:step-2"
    assert first.session_id != second.session_id

    assert build_step_session(None, "step-1", db_path) is None
