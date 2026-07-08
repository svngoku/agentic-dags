"""Usage metrics, cost estimation, and budget guards for agent runs.

Observability layer for the work-order flow: a ``UsageTracker`` records the
aggregated token usage of each ``Runner.run`` result under a label (``"plan"``,
``"step:<id>"``, ``"synthesize"``, ``"memory"``), prices it against
``DEFAULT_MODEL_PRICES`` (env overrides win), and rolls everything up into a
``RunMetrics`` snapshot. ``BudgetGuard`` turns a snapshot into an exceeded/ok
decision so the flow can stop scheduling further work, and
``publish_metrics_artifact`` renders the snapshot as a Prefect markdown
artifact (best effort — it never raises, even outside a Prefect run context).

Usage extraction is duck-typed: any object exposing ``context_wrapper.usage``
(the SDK's ``agents.usage.Usage``) or a bare ``usage`` attribute works, so
tests can pass ``types.SimpleNamespace`` fakes.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from prefect.artifacts import create_markdown_artifact
from prefect.context import FlowRunContext, TaskRunContext
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: Approximate USD prices per **1M tokens** as ``(input, output)``. These are
#: template defaults only — verify against the OpenAI pricing page and
#: override via ``LLM_PRICE_INPUT_PER_1M`` / ``LLM_PRICE_OUTPUT_PER_1M``.
DEFAULT_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "o3": (2.00, 8.00),
    "o4-mini": (1.10, 4.40),
}

_TOKENS_PER_MILLION = 1_000_000


def _env_float(name: str) -> float | None:
    """Parse an optional float environment variable.

    Unset or blank variables yield ``None``; anything non-numeric raises a
    ``ValueError`` naming the offending variable.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(name: str) -> int | None:
    """Parse an optional integer environment variable (see ``_env_float``)."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


class UsageEntry(BaseModel):
    """Token usage (and estimated cost) of one labeled ``Runner.run`` call."""

    label: str
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None
    usage_missing: bool = False


class RunMetrics(BaseModel):
    """Aggregated usage across all recorded entries of one flow run."""

    model: str
    entries: list[UsageEntry] = Field(default_factory=list)
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None
    unpriced_labels: list[str] = Field(default_factory=list)


class BudgetStatus(BaseModel):
    """Result of evaluating ``RunMetrics`` against a ``BudgetGuard``."""

    exceeded: bool = False
    reason: str | None = None


def resolve_model_prices(model: str) -> tuple[float, float] | None:
    """Return ``(input, output)`` USD prices per 1M tokens for ``model``.

    ``LLM_PRICE_INPUT_PER_1M`` / ``LLM_PRICE_OUTPUT_PER_1M`` each override the
    corresponding side of the ``DEFAULT_MODEL_PRICES`` entry (they apply to the
    configured model regardless of the table). Returns ``None`` when either
    side remains unknown — that model's usage is then recorded unpriced.
    """
    table_prices = DEFAULT_MODEL_PRICES.get(model)
    input_price = _env_float("LLM_PRICE_INPUT_PER_1M")
    output_price = _env_float("LLM_PRICE_OUTPUT_PER_1M")
    if input_price is None:
        input_price = table_prices[0] if table_prices else None
    if output_price is None:
        output_price = table_prices[1] if table_prices else None
    if input_price is None or output_price is None:
        return None
    return (input_price, output_price)


def estimate_cost_usd(
    input_tokens: int, output_tokens: int, prices: tuple[float, float] | None
) -> float | None:
    """Estimate the USD cost of a call, or ``None`` when ``prices`` is unknown."""
    if prices is None:
        return None
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / _TOKENS_PER_MILLION


def _extract_usage(result: Any) -> Any | None:
    """Pull the aggregated usage object off a ``RunResult``-like object.

    The Agents SDK exposes run usage at ``result.context_wrapper.usage``
    (an ``agents.usage.Usage``); a bare ``result.usage`` is accepted as a
    fallback. Returns ``None`` when neither is available.
    """
    context_wrapper = getattr(result, "context_wrapper", None)
    usage = getattr(context_wrapper, "usage", None)
    if usage is None:
        usage = getattr(result, "usage", None)
    return usage


class UsageTracker:
    """Accumulates per-call usage entries for one flow run.

    Prices are resolved once at construction time (table + env overrides),
    so all entries of a run are priced consistently.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self._prices = resolve_model_prices(model)
        self._entries: list[UsageEntry] = []

    def record(self, label: str, result: Any) -> UsageEntry:
        """Record the usage of one ``Runner.run`` result under ``label``.

        Results without usage data are recorded as zeros with
        ``usage_missing=True`` (and left unpriced) instead of raising.
        """
        usage = _extract_usage(result)
        if usage is None:
            logger.warning("No usage data on result for %r; recording zeros", label)
            entry = UsageEntry(label=label, usage_missing=True)
        else:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            entry = UsageEntry(
                label=label,
                requests=int(getattr(usage, "requests", 0) or 0),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
                estimated_cost_usd=estimate_cost_usd(input_tokens, output_tokens, self._prices),
            )
        self._entries.append(entry)
        return entry

    def metrics(self) -> RunMetrics:
        """Aggregate all recorded entries into a ``RunMetrics`` snapshot."""
        entries = list(self._entries)
        priced_costs = [
            entry.estimated_cost_usd for entry in entries if entry.estimated_cost_usd is not None
        ]
        return RunMetrics(
            model=self.model,
            entries=entries,
            requests=sum(entry.requests for entry in entries),
            input_tokens=sum(entry.input_tokens for entry in entries),
            output_tokens=sum(entry.output_tokens for entry in entries),
            total_tokens=sum(entry.total_tokens for entry in entries),
            estimated_cost_usd=sum(priced_costs) if priced_costs else None,
            unpriced_labels=[e.label for e in entries if e.estimated_cost_usd is None],
        )


class BudgetGuard(BaseModel):
    """Token / cost ceilings for a run; pure decision logic, no I/O.

    ``None`` disables a limit. Limits are inclusive: a run exactly at the
    limit is still allowed. When ``RunMetrics.estimated_cost_usd`` is ``None``
    (nothing was priceable), only the token limit can trip.
    """

    max_tokens: int | None = None
    max_cost_usd: float | None = None

    @classmethod
    def from_env(cls) -> BudgetGuard:
        """Read limits from ``MAX_TOKENS_BUDGET`` / ``MAX_COST_USD``.

        Unset or blank variables disable the corresponding limit.
        """
        return cls(
            max_tokens=_env_int("MAX_TOKENS_BUDGET"),
            max_cost_usd=_env_float("MAX_COST_USD"),
        )

    def status(self, metrics: RunMetrics) -> BudgetStatus:
        """Evaluate ``metrics`` against the configured limits."""
        if self.max_tokens is not None and metrics.total_tokens > self.max_tokens:
            return BudgetStatus(
                exceeded=True,
                reason=(
                    f"Token budget exceeded: {metrics.total_tokens:,} tokens "
                    f"> limit {self.max_tokens:,}"
                ),
            )
        cost = metrics.estimated_cost_usd
        if self.max_cost_usd is not None and cost is not None and cost > self.max_cost_usd:
            return BudgetStatus(
                exceeded=True,
                reason=f"Cost budget exceeded: ${cost:.4f} > limit ${self.max_cost_usd:.4f}",
            )
        return BudgetStatus(exceeded=False, reason=None)


def _format_cost(cost: float | None) -> str:
    """Format a cost cell for the markdown table (``—`` when unpriced)."""
    return "—" if cost is None else f"{cost:.4f}"


def render_metrics_markdown(metrics: RunMetrics) -> str:
    """Render ``metrics`` as a compact markdown table for reports/artifacts."""
    lines = [
        f"### LLM usage — model `{metrics.model}`",
        "",
        "| Label | Requests | Input | Output | Total | Est. cost (USD) |",
        "| :-- | --: | --: | --: | --: | --: |",
    ]
    for entry in metrics.entries:
        lines.append(
            f"| {entry.label} | {entry.requests:,} | {entry.input_tokens:,} "
            f"| {entry.output_tokens:,} | {entry.total_tokens:,} "
            f"| {_format_cost(entry.estimated_cost_usd)} |"
        )
    lines.append(
        f"| **Total** | **{metrics.requests:,}** | **{metrics.input_tokens:,}** "
        f"| **{metrics.output_tokens:,}** | **{metrics.total_tokens:,}** "
        f"| **{_format_cost(metrics.estimated_cost_usd)}** |"
    )
    missing = [entry.label for entry in metrics.entries if entry.usage_missing]
    if missing:
        lines += ["", f"_Usage unavailable (recorded as zeros): {', '.join(missing)}._"]
    if metrics.unpriced_labels:
        lines += ["", f"_No cost estimate for: {', '.join(metrics.unpriced_labels)}._"]
    if metrics.estimated_cost_usd is not None:
        lines += ["", "_Costs are estimates based on approximate per-1M-token prices._"]
    return "\n".join(lines)


def _in_prefect_run_context() -> bool:
    """Whether an active Prefect flow or task run context is available."""
    return FlowRunContext.get() is not None or TaskRunContext.get() is not None


def publish_metrics_artifact(metrics: RunMetrics, key: str = "run-metrics") -> str | None:
    """Publish ``metrics`` as a Prefect markdown artifact (best effort).

    Outside a Prefect run context this is a no-op, and any publishing failure
    is logged and swallowed — metrics reporting must never break a run.
    Returns the artifact id as a string, or ``None`` when skipped/failed.
    """
    if not _in_prefect_run_context():
        logger.debug("No active Prefect run context; skipping metrics artifact")
        return None
    try:
        artifact_id = create_markdown_artifact(
            markdown=render_metrics_markdown(metrics),
            key=key,
            description="LLM usage and estimated cost for this run",
        )
    except Exception:
        logger.warning("Failed to publish metrics artifact; continuing", exc_info=True)
        return None
    return str(artifact_id)
