from __future__ import annotations

import asyncio

from openai.types.responses import ResponseTextDeltaEvent
from agents import Agent, Runner, ModelSettings

from src.config import AppConfig, build_run_config, configure_openai
from src.wrapper.sessions import build_session
from src.wrapper.tools import summarize_text


async def main() -> None:
    config = AppConfig.from_env()
    config.validate_required()
    configure_openai(config)

    agent = Agent(
        name="InteractiveAssistant",
        instructions=(
            "You are a concise assistant. Use summarize_text when helpful."
        ),
        tools=[summarize_text],
        model=config.model,
        model_settings=ModelSettings(include_usage=True),
    )

    run_config = build_run_config(config)
    session = build_session(config.session_id or "interactive", config.session_db_path)

    result = Runner.run_streamed(
        agent,
        "Summarize this: The team shipped v2, reduced latency, and opened two follow-ups.",
        run_config=run_config,
        session=session,
    )

    async for event in result.stream_events():
        if event.type == "raw_response_event" and isinstance(
            event.data, ResponseTextDeltaEvent
        ):
            print(event.data.delta, end="", flush=True)

    print("\n")


if __name__ == "__main__":
    asyncio.run(main())
