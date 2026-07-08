"""Application configuration and agent wiring.

Centralizes environment parsing (via pydantic), OpenAI client / tracing setup,
and construction of the supervisor / executor / writer agents.
"""

from __future__ import annotations

import os
from typing import NamedTuple

from agents import Agent, RunConfig, Tool, set_default_openai_key, set_tracing_disabled
from agents.mcp import MCPServer
from agents.tracing import set_tracing_export_api_key
from dotenv import load_dotenv
from pydantic import BaseModel

from src.agents.executor import build_executor
from src.agents.supervisor import build_supervisor
from src.agents.writer import build_writer
from src.wrapper.guardrails import build_input_guardrails, build_output_guardrails
from src.wrapper.mcp import build_hosted_mcp_tool
from src.wrapper.tools import summarize_text

_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    ``"1"``, ``"true"``, ``"yes"`` and ``"on"`` (case-insensitive) are truthy;
    unset or empty variables fall back to ``default``.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUTHY_VALUES


class AgentBundle(NamedTuple):
    """The three cooperating agents used by the work-order flow.

    Being a ``NamedTuple``, it still supports positional unpacking:
    ``supervisor, executor, writer = build_agents(config)``.
    """

    supervisor: Agent
    executor: Agent
    writer: Agent


class AppConfig(BaseModel):
    """Runtime configuration, typically loaded from the environment."""

    model: str = "gpt-4.1"
    openai_api_key: str
    tracing_api_key: str | None = None
    tracing_disabled: bool = False
    trace_include_sensitive_data: bool = False
    workflow_name: str = "Work Order Orchestrator"
    group_id: str | None = None
    session_id: str | None = None
    session_db_path: str = "sessions.db"
    auto_approve: bool = False
    use_hosted_mcp: bool = False

    @classmethod
    def from_env(cls) -> AppConfig:
        """Build a config from environment variables (loading ``.env`` first)."""
        load_dotenv()
        return cls(
            model=os.getenv("LLM_MODEL", "gpt-4.1"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            tracing_api_key=os.getenv("TRACING_API_KEY"),
            tracing_disabled=_env_bool("TRACING_DISABLED"),
            trace_include_sensitive_data=_env_bool("TRACE_INCLUDE_SENSITIVE_DATA"),
            workflow_name=os.getenv("WORKFLOW_NAME", "Work Order Orchestrator"),
            group_id=os.getenv("TRACE_GROUP_ID"),
            session_id=os.getenv("SESSION_ID"),
            session_db_path=os.getenv("SESSION_DB_PATH", "sessions.db"),
            auto_approve=_env_bool("AUTO_APPROVE"),
            use_hosted_mcp=_env_bool("USE_HOSTED_MCP"),
        )

    def validate_required(self) -> None:
        """Raise if required settings are missing."""
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required")


def configure_openai(config: AppConfig) -> None:
    """Apply API key and tracing settings to the OpenAI Agents SDK."""
    set_default_openai_key(config.openai_api_key)
    if config.tracing_api_key:
        set_tracing_export_api_key(config.tracing_api_key)
    set_tracing_disabled(config.tracing_disabled)


def build_run_config(config: AppConfig) -> RunConfig:
    """Build the shared ``RunConfig`` (guardrails + tracing) for all runs."""
    return RunConfig(
        input_guardrails=build_input_guardrails(),
        output_guardrails=build_output_guardrails(),
        tracing_disabled=config.tracing_disabled,
        trace_include_sensitive_data=config.trace_include_sensitive_data,
        workflow_name=config.workflow_name,
        group_id=config.group_id,
    )


def build_agents(
    config: AppConfig, mcp_servers: list[MCPServer] | None = None
) -> AgentBundle:
    """Build the supervisor / executor / writer agents.

    Uses the native OpenAI endpoint. When ``config.use_hosted_mcp`` is set,
    a hosted MCP tool is attached to every agent in addition to local tools.
    """
    configure_openai(config)
    tools: list[Tool] = [summarize_text]
    if config.use_hosted_mcp:
        tools.append(build_hosted_mcp_tool())

    return AgentBundle(
        supervisor=build_supervisor(config.model, mcp_servers=mcp_servers, tools=tools),
        executor=build_executor(config.model, mcp_servers=mcp_servers, tools=tools),
        writer=build_writer(config.model, mcp_servers=mcp_servers, tools=tools),
    )
