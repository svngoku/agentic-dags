from __future__ import annotations

from agents import SQLiteSession


def build_session(session_id: str | None, db_path: str) -> SQLiteSession | None:
    if not session_id:
        return None
    return SQLiteSession(session_id, db_path)


def build_step_session(
    session_id: str | None, step_id: str, db_path: str
) -> SQLiteSession | None:
    if not session_id:
        return None
    return SQLiteSession(f"{session_id}:{step_id}", db_path)
