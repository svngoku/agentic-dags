"""Unit tests for environment parsing and agent construction."""

from __future__ import annotations

import pytest

from src.config import AgentBundle, AppConfig, _env_bool, build_agents, build_run_config


@pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on", " yes "])
def test_env_bool_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SOME_FLAG", value)
    assert _env_bool("SOME_FLAG") is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "banana"])
def test_env_bool_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SOME_FLAG", value)
    assert _env_bool("SOME_FLAG") is False


def test_env_bool_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert _env_bool("SOME_FLAG") is False
    assert _env_bool("SOME_FLAG", default=True) is True
    monkeypatch.setenv("SOME_FLAG", "   ")
    assert _env_bool("SOME_FLAG", default=True) is True


def test_from_env_reads_variables(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # ensure no local .env interferes
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    monkeypatch.setenv("TRACING_DISABLED", "true")
    monkeypatch.setenv("AUTO_APPROVE", "1")
    monkeypatch.setenv("SESSION_ID", "sess-1")
    monkeypatch.delenv("USE_HOSTED_MCP", raising=False)

    config = AppConfig.from_env()

    assert config.openai_api_key == "test-key"
    assert config.model == "gpt-test"
    assert config.tracing_disabled is True
    assert config.auto_approve is True
    assert config.session_id == "sess-1"
    assert config.use_hosted_mcp is False


def test_validate_required_raises_without_key() -> None:
    config = AppConfig(openai_api_key="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        config.validate_required()


def test_validate_required_passes_with_key() -> None:
    AppConfig(openai_api_key="test-key").validate_required()


def test_build_run_config_carries_settings() -> None:
    config = AppConfig(
        openai_api_key="test-key",
        workflow_name="My Workflow",
        tracing_disabled=True,
        group_id="grp",
    )
    run_config = build_run_config(config)
    assert run_config.workflow_name == "My Workflow"
    assert run_config.tracing_disabled is True
    assert run_config.group_id == "grp"
    assert run_config.input_guardrails
    assert run_config.output_guardrails


def test_build_agents_returns_named_bundle() -> None:
    config = AppConfig(openai_api_key="test-key", tracing_disabled=True)
    bundle = build_agents(config)

    assert isinstance(bundle, AgentBundle)
    assert bundle.supervisor.name == "Supervisor"
    assert bundle.executor.name == "Executor"
    assert bundle.writer.name == "Writer"

    # NamedTuple stays compatible with positional unpacking.
    supervisor, executor, writer = bundle
    assert (supervisor, executor, writer) == bundle
