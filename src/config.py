from __future__ import annotations

import os
from typing import List

from dotenv import load_dotenv
from pydantic import BaseModel

from agents import Agent, RunConfig, set_default_openai_key, set_tracing_disabled
from agents.mcp import MCPServer
from agents.tracing import set_tracing_export_api_key

from src.agents.executor import build_executor
from src.agents.supervisor import build_supervisor
from src.agents.writer import build_writer
from src.wrapper.guardrails import build_input_guardrails, build_output_guardrails
from src.wrapper.mcp import build_hosted_mcp_tool
from src.wrapper.tools import summarize_text


class AppConfig(BaseModel):
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
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        return cls(
            model=os.getenv("LLM_MODEL", "gpt-4.1"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            tracing_api_key=os.getenv("TRACING_API_KEY"),
            tracing_disabled=os.getenv("TRACING_DISABLED", "").lower() in ("1", "true", "yes"),
            trace_include_sensitive_data=os.getenv("TRACE_INCLUDE_SENSITIVE_DATA", "").lower()
            in ("1", "true", "yes"),
            workflow_name=os.getenv("WORKFLOW_NAME", "Work Order Orchestrator"),
            group_id=os.getenv("TRACE_GROUP_ID"),
            session_id=os.getenv("SESSION_ID"),
            session_db_path=os.getenv("SESSION_DB_PATH", "sessions.db"),
            auto_approve=os.getenv("AUTO_APPROVE", "").lower() in ("1", "true", "yes"),
            use_hosted_mcp=os.getenv("USE_HOSTED_MCP", "").lower() in ("1", "true", "yes"),
        )

    def validate_required(self) -> None:
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required")


def configure_openai(config: AppConfig) -> None:
    set_default_openai_key(config.openai_api_key)
    if config.tracing_api_key:
        set_tracing_export_api_key(config.tracing_api_key)
    set_tracing_disabled(config.tracing_disabled)


def build_run_config(config: AppConfig) -> RunConfig:
    return RunConfig(
        input_guardrails=build_input_guardrails(),
        output_guardrails=build_output_guardrails(),
        tracing_disabled=config.tracing_disabled,
        trace_include_sensitive_data=config.trace_include_sensitive_data,
        workflow_name=config.workflow_name,
        group_id=config.group_id,
    )


def build_agents(
    config: AppConfig, mcp_servers: List[MCPServer] | None = None
) -> tuple[Agent, Agent, Agent]:
    """Build agents using the native OpenAI endpoint."""
    configure_openai(config)
    tools: list = [summarize_text]
    if config.use_hosted_mcp:
        tools.append(build_hosted_mcp_tool())

    supervisor = build_supervisor(config.model, mcp_servers=mcp_servers, tools=tools)
    executor = build_executor(config.model, mcp_servers=mcp_servers, tools=tools)
    writer = build_writer(config.model, mcp_servers=mcp_servers, tools=tools)
    return supervisor, executor, writer
