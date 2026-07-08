"""Unit tests for usage tracking, cost estimation, and budget guards."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import src.wrapper.metrics as metrics_module
from src.wrapper.metrics import (
    DEFAULT_MODEL_PRICES,
    BudgetGuard,
    RunMetrics,
    UsageTracker,
    publish_metrics_artifact,
    render_metrics_markdown,
    resolve_model_prices,
)

_METRICS_ENV_VARS = (
    "LLM_PRICE_INPUT_PER_1M",
    "LLM_PRICE_OUTPUT_PER_1M",
    "MAX_TOKENS_BUDGET",
    "MAX_COST_USD",
)


@pytest.fixture(autouse=True)
def _clean_metrics_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ambient pricing/budget variables from leaking into tests."""
    for name in _METRICS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _fake_result(
    requests: int = 1,
    input_tokens: int = 100,
    output_tokens: int = 50,
    total_tokens: int | None = None,
) -> SimpleNamespace:
    """Mimic a ``RunResult`` exposing ``context_wrapper.usage``."""
    usage = SimpleNamespace(
        requests=requests,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens if total_tokens is None else total_tokens,
    )
    return SimpleNamespace(context_wrapper=SimpleNamespace(usage=usage))


# ---- UsageTracker aggregation ----


def test_tracker_aggregates_entries() -> None:
    tracker = UsageTracker("gpt-4.1")
    tracker.record("plan", _fake_result(requests=1, input_tokens=100, output_tokens=50))
    tracker.record("step:a", _fake_result(requests=2, input_tokens=200, output_tokens=100))
    tracker.record("synthesize", _fake_result(requests=1, input_tokens=300, output_tokens=150))

    metrics = tracker.metrics()

    assert metrics.model == "gpt-4.1"
    assert [entry.label for entry in metrics.entries] == ["plan", "step:a", "synthesize"]
    assert metrics.requests == 4
    assert metrics.input_tokens == 600
    assert metrics.output_tokens == 300
    assert metrics.total_tokens == 900
    assert metrics.unpriced_labels == []


def test_record_returns_the_new_entry() -> None:
    tracker = UsageTracker("gpt-4.1")
    entry = tracker.record("plan", _fake_result(input_tokens=10, output_tokens=5))
    assert entry.label == "plan"
    assert entry.total_tokens == 15
    assert entry.usage_missing is False


def test_missing_usage_records_zeros_and_flag() -> None:
    tracker = UsageTracker("gpt-4.1")

    no_wrapper = tracker.record("memory", SimpleNamespace())
    assert no_wrapper.usage_missing is True
    assert no_wrapper.requests == 0
    assert no_wrapper.total_tokens == 0
    assert no_wrapper.estimated_cost_usd is None

    none_usage = tracker.record(
        "plan", SimpleNamespace(context_wrapper=SimpleNamespace(usage=None))
    )
    assert none_usage.usage_missing is True

    metrics = tracker.metrics()
    assert metrics.total_tokens == 0
    assert metrics.estimated_cost_usd is None
    assert metrics.unpriced_labels == ["memory", "plan"]


# ---- Cost math ----


def test_cost_known_model_uses_default_prices() -> None:
    input_price, output_price = DEFAULT_MODEL_PRICES["gpt-4.1-mini"]
    tracker = UsageTracker("gpt-4.1-mini")

    entry = tracker.record(
        "plan", _fake_result(input_tokens=1_000_000, output_tokens=500_000)
    )

    expected = (1_000_000 * input_price + 500_000 * output_price) / 1_000_000
    assert entry.estimated_cost_usd == pytest.approx(expected)
    assert tracker.metrics().estimated_cost_usd == pytest.approx(expected)


def test_cost_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_1M", "3.0")
    monkeypatch.setenv("LLM_PRICE_OUTPUT_PER_1M", "6.0")

    # The overrides apply even to models missing from the price table.
    tracker = UsageTracker("acme-custom-model")
    entry = tracker.record(
        "plan", _fake_result(input_tokens=2_000_000, output_tokens=1_000_000)
    )
    assert entry.estimated_cost_usd == pytest.approx(2 * 3.0 + 1 * 6.0)

    # And they beat the table for known models too.
    assert resolve_model_prices("gpt-4.1") == (3.0, 6.0)


def test_resolve_model_prices_partial_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_1M", "9.0")
    assert resolve_model_prices("gpt-4.1") == (9.0, DEFAULT_MODEL_PRICES["gpt-4.1"][1])
    # Unknown model with only one side overridden stays unpriceable.
    assert resolve_model_prices("acme-custom-model") is None


def test_cost_unknown_model_is_unpriced() -> None:
    tracker = UsageTracker("acme-custom-model")
    entry = tracker.record("plan", _fake_result())

    assert entry.estimated_cost_usd is None
    metrics = tracker.metrics()
    assert metrics.estimated_cost_usd is None
    assert metrics.unpriced_labels == ["plan"]


# ---- BudgetGuard ----


def test_budget_guard_disabled_by_default() -> None:
    guard = BudgetGuard.from_env()
    assert guard.max_tokens is None
    assert guard.max_cost_usd is None

    metrics = RunMetrics(model="m", total_tokens=10**9, estimated_cost_usd=10**6)
    status = guard.status(metrics)
    assert status.exceeded is False
    assert status.reason is None


def test_budget_guard_blank_env_disables_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TOKENS_BUDGET", "   ")
    monkeypatch.setenv("MAX_COST_USD", "")
    guard = BudgetGuard.from_env()
    assert guard.max_tokens is None
    assert guard.max_cost_usd is None


def test_budget_guard_token_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TOKENS_BUDGET", "100")
    guard = BudgetGuard.from_env()

    status = guard.status(RunMetrics(model="m", total_tokens=150))
    assert status.exceeded is True
    assert "token" in (status.reason or "").lower()


def test_budget_guard_cost_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_COST_USD", "0.5")
    guard = BudgetGuard.from_env()

    status = guard.status(RunMetrics(model="m", total_tokens=10, estimated_cost_usd=0.75))
    assert status.exceeded is True
    assert "cost" in (status.reason or "").lower()


def test_budget_guard_cost_limit_needs_priced_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_COST_USD", "0.5")
    guard = BudgetGuard.from_env()

    # Unpriced runs can only trip the token limit — none is set here.
    metrics = RunMetrics(model="m", total_tokens=10**9, estimated_cost_usd=None)
    assert guard.status(metrics).exceeded is False


def test_budget_guard_ok_at_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TOKENS_BUDGET", "1000")
    monkeypatch.setenv("MAX_COST_USD", "1.0")
    guard = BudgetGuard.from_env()

    status = guard.status(RunMetrics(model="m", total_tokens=1000, estimated_cost_usd=1.0))
    assert status.exceeded is False
    assert status.reason is None


def test_budget_guard_from_env_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TOKENS_BUDGET", "lots")
    with pytest.raises(ValueError, match="MAX_TOKENS_BUDGET"):
        BudgetGuard.from_env()


# ---- Markdown rendering ----


def test_render_metrics_markdown_contains_totals() -> None:
    tracker = UsageTracker("gpt-4.1")
    tracker.record("plan", _fake_result(requests=1, input_tokens=1_200, output_tokens=300))
    tracker.record("step:fetch", _fake_result(requests=2, input_tokens=2_000, output_tokens=500))
    metrics = tracker.metrics()

    markdown = render_metrics_markdown(metrics)

    assert "`gpt-4.1`" in markdown
    assert "| plan |" in markdown
    assert "| step:fetch |" in markdown
    assert f"**{metrics.requests:,}**" in markdown
    assert f"**{metrics.total_tokens:,}**" in markdown  # 4,000
    assert metrics.estimated_cost_usd is not None
    assert f"{metrics.estimated_cost_usd:.4f}" in markdown


def test_render_metrics_markdown_flags_unpriced_and_missing() -> None:
    tracker = UsageTracker("acme-custom-model")
    tracker.record("plan", _fake_result())
    tracker.record("memory", SimpleNamespace())

    markdown = render_metrics_markdown(tracker.metrics())

    assert "No cost estimate for: plan, memory" in markdown
    assert "Usage unavailable (recorded as zeros): memory" in markdown
    assert "—" in markdown  # unpriced cost cells


# ---- Artifact publishing ----


def test_publish_metrics_artifact_outside_run_context() -> None:
    tracker = UsageTracker("gpt-4.1")
    tracker.record("plan", _fake_result())
    # Must be a silent no-op without an active Prefect flow/task run.
    assert publish_metrics_artifact(tracker.metrics(), key="test-metrics") is None


def test_publish_metrics_artifact_swallows_publish_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("no artifact for you")

    monkeypatch.setattr(metrics_module, "_in_prefect_run_context", lambda: True)
    monkeypatch.setattr(metrics_module, "create_markdown_artifact", _boom)

    tracker = UsageTracker("gpt-4.1")
    tracker.record("plan", _fake_result())
    assert publish_metrics_artifact(tracker.metrics()) is None
