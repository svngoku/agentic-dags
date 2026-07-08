"""Resilience helpers for LLM agent runs: retries, model fallback, bounded fan-out.

``run_with_resilience`` is a drop-in wrapper for ``agents.Runner.run`` that
retries transient failures with capped exponential backoff (plus optional
jitter) and, once the primary model exhausts its attempts, can re-run the
agent cloned onto a fallback model. ``gather_with_concurrency`` bounds how
many step coroutines run at once while preserving ``asyncio.gather`` ordering.

Retry classification rationale (see ``is_transient``):

Transient — retried:
    * ``openai.RateLimitError`` (429), ``openai.APITimeoutError``,
      ``openai.APIConnectionError``, ``openai.InternalServerError``, and any
      other ``openai.APIStatusError`` with ``status_code >= 500``: server or
      network conditions that typically clear on their own.
    * ``agents.exceptions.ModelBehaviorError``: deliberately RETRYABLE. It
      signals malformed model output (invalid JSON for the output schema, a
      call to a tool that does not exist) — a nondeterministic sampling
      artifact, so a fresh attempt frequently parses cleanly and is far
      cheaper than failing the whole Prefect task.

Non-transient — raised immediately:
    * Guardrail tripwires (``InputGuardrailTripwireTriggered`` etc.):
      deterministic policy verdicts; retrying spends tokens to reach the same
      verdict and would amount to retrying past a safety control.
    * ``MaxTurnsExceeded``: the run structurally failed to converge within
      budget; an identical retry repeats the full cost with the same likely
      outcome. ``UserError`` and other ``AgentsException``: SDK misuse.
    * OpenAI auth/permission errors (401/403) and every other 4xx status:
      deterministic client-side failures.
    * Unknown exception types default to non-transient (fail fast; the
      Prefect task layer's own ``retries=2`` remains a coarse outer net).

Retried attempts reuse the same ``session``; this mirrors the semantics of
Prefect task-level retries already present in the flow.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from agents import Agent, RunConfig, Runner, RunResult
from agents.exceptions import AgentsException, ModelBehaviorError
from agents.memory import Session
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0
DEFAULT_MAX_DELAY_SECONDS = 30.0
DEFAULT_MAX_TURNS = 10

RunnerFn = Callable[..., Awaitable[RunResult]]
SleepFn = Callable[[float], Awaitable[Any]]

#: OpenAI client errors that are always safe to retry.
_TRANSIENT_OPENAI_ERRORS: tuple[type[Exception], ...] = (
    openai.RateLimitError,
    openai.APITimeoutError,  # subclass of APIConnectionError; listed for clarity
    openai.APIConnectionError,
    openai.InternalServerError,
)


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment variable, falling back on unset/blank."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _env_float(name: str, default: float) -> float:
    """Parse a float environment variable, falling back on unset/blank."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw.strip())


class RetryPolicy(BaseModel):
    """Backoff policy for retrying transient LLM-call failures."""

    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1)
    base_delay_seconds: float = Field(default=DEFAULT_BASE_DELAY_SECONDS, ge=0.0)
    max_delay_seconds: float = Field(default=DEFAULT_MAX_DELAY_SECONDS, ge=0.0)
    jitter: bool = True

    @classmethod
    def from_env(cls) -> RetryPolicy:
        """Build a policy from ``LLM_MAX_ATTEMPTS`` / ``LLM_RETRY_*`` env vars."""
        return cls(
            max_attempts=_env_int("LLM_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
            base_delay_seconds=_env_float("LLM_RETRY_BASE_DELAY", DEFAULT_BASE_DELAY_SECONDS),
            max_delay_seconds=_env_float("LLM_RETRY_MAX_DELAY", DEFAULT_MAX_DELAY_SECONDS),
        )

    def delay_for_attempt(self, attempt: int) -> float:
        """Delay before retrying after failed attempt ``attempt`` (1-based).

        ``min(base * 2**(attempt - 1), max)`` plus uniform jitter in
        ``[0, delay / 2]`` when ``jitter`` is enabled.
        """
        delay = min(self.base_delay_seconds * 2 ** (attempt - 1), self.max_delay_seconds)
        if self.jitter:
            delay += random.uniform(0.0, delay / 2)
        return delay


def is_transient(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` is worth retrying (see module docstring)."""
    if isinstance(exc, ModelBehaviorError):
        return True
    if isinstance(exc, AgentsException):
        return False
    if isinstance(exc, _TRANSIENT_OPENAI_ERRORS):
        return True
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", None)
        return status is not None and status >= 500
    return False


def fallback_model_from_env() -> str | None:
    """Read ``LLM_FALLBACK_MODEL``; empty/unset means no fallback."""
    raw = os.getenv("LLM_FALLBACK_MODEL", "").strip()
    return raw or None


def max_concurrent_steps_from_env() -> int:
    """Read ``MAX_CONCURRENT_STEPS`` (``0`` or unset means unlimited)."""
    return _env_int("MAX_CONCURRENT_STEPS", 0)


async def _run_attempts(
    agent: Agent,
    input_text: str,
    *,
    runner_fn: RunnerFn,
    policy: RetryPolicy,
    sleep: SleepFn,
    phase: str,
    max_turns: int,
    run_config: RunConfig | None,
    session: Session | None,
) -> RunResult:
    """Run up to ``policy.max_attempts`` attempts; re-raise the last transient error."""
    last_error: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await runner_fn(
                agent,
                input_text,
                max_turns=max_turns,
                run_config=run_config,
                session=session,
            )
        except Exception as exc:
            if not is_transient(exc):
                raise
            last_error = exc
            if attempt < policy.max_attempts:
                delay = policy.delay_for_attempt(attempt)
                logger.warning(
                    "%s attempt %d/%d failed with %s: %s; retrying in %.2fs",
                    phase,
                    attempt,
                    policy.max_attempts,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await sleep(delay)
    if last_error is None:  # pragma: no cover - max_attempts >= 1 guarantees an outcome
        raise RuntimeError("retry loop exited without an outcome")
    raise last_error


async def run_with_resilience(
    agent: Agent,
    input_text: str,
    *,
    run_config: RunConfig | None = None,
    session: Session | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    policy: RetryPolicy | None = None,
    fallback_model: str | None = None,
    runner_fn: RunnerFn | None = None,
    sleep: SleepFn | None = None,
) -> RunResult:
    """Run ``agent`` via ``runner_fn`` (default ``Runner.run``) with retries.

    Transient failures (see ``is_transient``) are retried per ``policy`` with
    exponential backoff. If every attempt on the primary model fails
    transiently and ``fallback_model`` is set, the agent is cloned onto the
    fallback model and given ``policy.max_attempts`` more tries. Non-transient
    errors propagate immediately. ``runner_fn`` and ``sleep`` are injectable
    for offline tests.
    """
    active_policy = policy if policy is not None else RetryPolicy.from_env()
    active_runner: RunnerFn = runner_fn if runner_fn is not None else Runner.run
    active_sleep: SleepFn = sleep if sleep is not None else asyncio.sleep
    try:
        return await _run_attempts(
            agent,
            input_text,
            runner_fn=active_runner,
            policy=active_policy,
            sleep=active_sleep,
            phase=f"primary[{getattr(agent, 'name', agent)!s}]",
            max_turns=max_turns,
            run_config=run_config,
            session=session,
        )
    except Exception as exc:
        if fallback_model is None or not is_transient(exc):
            raise
        logger.warning(
            "Primary model exhausted %d attempt(s) (last error: %s); "
            "falling back to model %r",
            active_policy.max_attempts,
            type(exc).__name__,
            fallback_model,
        )
        fallback_agent = agent.clone(model=fallback_model)
        return await _run_attempts(
            fallback_agent,
            input_text,
            runner_fn=active_runner,
            policy=active_policy,
            sleep=active_sleep,
            phase=f"fallback[{fallback_model}]",
            max_turns=max_turns,
            run_config=run_config,
            session=session,
        )


async def gather_with_concurrency(
    limit: int,
    *aws: Awaitable[Any],
    return_exceptions: bool = False,
) -> list[Any]:
    """Order-preserving ``asyncio.gather`` running at most ``limit`` awaitables at once.

    ``limit <= 0`` means unlimited (plain ``asyncio.gather``). Results (and,
    with ``return_exceptions=True``, exceptions) keep the input order.
    """
    if limit <= 0:
        return await asyncio.gather(*aws, return_exceptions=return_exceptions)

    semaphore = asyncio.Semaphore(limit)

    async def _bounded(aw: Awaitable[Any]) -> Any:
        async with semaphore:
            return await aw

    return await asyncio.gather(
        *(_bounded(aw) for aw in aws), return_exceptions=return_exceptions
    )
