"""SQLite-backed session builders.

Sessions are optional: when no ``session_id`` is configured the builders
return ``None`` and runs proceed without conversation memory.
"""

from __future__ import annotations

from agents import SQLiteSession


def build_session(session_id: str | None, db_path: str) -> SQLiteSession | None:
    """Build the main session for a run, or ``None`` when sessions are disabled."""
    if not session_id:
        return None
    return SQLiteSession(session_id, db_path)


def build_step_session(
    session_id: str | None, step_id: str, db_path: str
) -> SQLiteSession | None:
    """Build an isolated per-step session so parallel steps don't interleave."""
    if not session_id:
        return None
    return SQLiteSession(f"{session_id}:{step_id}", db_path)
