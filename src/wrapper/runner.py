"""Thin helpers around ``agents.Runner`` for common run patterns."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agents import Agent, RunConfig, Runner, RunResult
from agents.memory import Session
from openai.types.responses import ResponseTextDeltaEvent

DEFAULT_MAX_TURNS = 10


async def run_agent(
    agent: Agent,
    input_text: str,
    *,
    run_config: RunConfig | None = None,
    session: Session | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> RunResult:
    """Run ``agent`` to completion and return the full ``RunResult``."""
    return await Runner.run(
        agent,
        input_text,
        run_config=run_config,
        session=session,
        max_turns=max_turns,
    )


async def stream_text(
    agent: Agent,
    input_text: str,
    *,
    run_config: RunConfig | None = None,
    session: Session | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> AsyncIterator[str]:
    """Run ``agent`` with streaming and yield text deltas as they arrive."""
    result = Runner.run_streamed(
        agent,
        input_text,
        run_config=run_config,
        session=session,
        max_turns=max_turns,
    )
    async for event in result.stream_events():
        if event.type == "raw_response_event" and isinstance(
            event.data, ResponseTextDeltaEvent
        ):
            yield event.data.delta
