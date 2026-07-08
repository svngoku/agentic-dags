"""Streaming + sessions demo: a minimal assistant that streams its answer.

Run with ``uv run python -m src.projects.interactive_assistant`` or pass a
custom prompt: ``uv run python -m src.projects.interactive_assistant "..."``.
"""

from __future__ import annotations

import asyncio
import sys

from agents import Agent, ModelSettings

from src.config import AppConfig, build_run_config, configure_openai
from src.wrapper.runner import stream_text
from src.wrapper.sessions import build_session
from src.wrapper.tools import summarize_text

DEFAULT_PROMPT = (
    "Summarize this: The team shipped v2, reduced latency, and opened two follow-ups."
)
DEFAULT_SESSION_ID = "interactive"


async def main(prompt: str = DEFAULT_PROMPT) -> None:
    """Stream the assistant's answer for ``prompt`` to stdout."""
    config = AppConfig.from_env()
    config.validate_required()
    configure_openai(config)

    agent = Agent(
        name="InteractiveAssistant",
        instructions="You are a concise assistant. Use summarize_text when helpful.",
        tools=[summarize_text],
        model=config.model,
        model_settings=ModelSettings(include_usage=True),
    )

    run_config = build_run_config(config)
    session = build_session(
        config.session_id or DEFAULT_SESSION_ID, config.session_db_path
    )

    async for delta in stream_text(agent, prompt, run_config=run_config, session=session):
        print(delta, end="", flush=True)

    print("\n")


if __name__ == "__main__":
    cli_prompt = " ".join(sys.argv[1:]).strip() or DEFAULT_PROMPT
    asyncio.run(main(cli_prompt))
