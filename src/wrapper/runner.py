from __future__ import annotations

from typing import Any, AsyncIterator

from openai.types.responses import ResponseTextDeltaEvent
from agents import Agent, RunConfig, Runner


def run_agent(
    agent: Agent,
    input_text: str,
    *,
    run_config: RunConfig | None = None,
    session=None,
    max_turns: int = 10,
):
    return Runner.run(
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
    session=None,
    max_turns: int = 10,
) -> AsyncIterator[str]:
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
