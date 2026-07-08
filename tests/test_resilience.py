"""Offline tests for the LLM-call resilience helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    ModelBehaviorError,
    UserError,
)

from src.wrapper.resilience import (
    RetryPolicy,
    fallback_model_from_env,
    gather_with_concurrency,
    is_transient,
    max_concurrent_steps_from_env,
    run_with_resilience,
)

_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


def _status_error(cls: type, status: int, message: str = "boom") -> Exception:
    return cls(message, response=httpx.Response(status, request=_REQUEST), body=None)


def _tripwire() -> InputGuardrailTripwireTriggered:
    return InputGuardrailTripwireTriggered(SimpleNamespace(guardrail=object()))


class FakeAgent:
    """Duck-typed agent recording ``clone`` calls."""

    def __init__(self, name: str = "primary", model: str = "gpt-primary") -> None:
        self.name = name
        self.model = model
        self.clone_calls: list[dict[str, Any]] = []

    def clone(self, **kwargs: Any) -> FakeAgent:
        self.clone_calls.append(kwargs)
        return FakeAgent(name=f"{self.name}-fallback", model=kwargs["model"])


def make_runner(outcomes: list[Any]) -> tuple[Any, list[tuple[Any, str, dict[str, Any]]]]:
    """Fake runner_fn returning/raising ``outcomes`` in order, recording calls."""
    calls: list[tuple[Any, str, dict[str, Any]]] = []

    async def runner_fn(agent: Any, input_text: str, **kwargs: Any) -> Any:
        calls.append((agent, input_text, kwargs))
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return runner_fn, calls


def make_sleeper() -> tuple[Any, list[float]]:
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    return sleep, delays


def _policy(**overrides: Any) -> RetryPolicy:
    defaults: dict[str, Any] = {
        "max_attempts": 3,
        "base_delay_seconds": 1.0,
        "max_delay_seconds": 30.0,
        "jitter": False,
    }
    defaults.update(overrides)
    return RetryPolicy(**defaults)


# ---- run_with_resilience ----


async def test_success_first_try_does_not_sleep() -> None:
    runner_fn, calls = make_runner([{"ok": True}])
    sleep, delays = make_sleeper()

    result = await run_with_resilience(
        FakeAgent(), "hi", policy=_policy(), runner_fn=runner_fn, sleep=sleep
    )

    assert result == {"ok": True}
    assert len(calls) == 1
    assert delays == []


async def test_transient_failures_then_success_with_backoff() -> None:
    errors = [_status_error(openai.RateLimitError, 429), _status_error(openai.RateLimitError, 429)]
    runner_fn, calls = make_runner([*errors, "done"])
    sleep, delays = make_sleeper()

    result = await run_with_resilience(
        FakeAgent(), "hi", policy=_policy(), runner_fn=runner_fn, sleep=sleep
    )

    assert result == "done"
    assert len(calls) == 3
    assert delays == [1.0, 2.0]  # base * 2**(attempt-1), jitter disabled


def test_delay_curve_caps_at_max_and_jitter_is_bounded() -> None:
    capped = _policy(base_delay_seconds=10.0, max_delay_seconds=15.0)
    assert [capped.delay_for_attempt(n) for n in (1, 2, 3)] == [10.0, 15.0, 15.0]
    jittered = _policy(jitter=True)
    for _ in range(50):
        assert 1.0 <= jittered.delay_for_attempt(1) <= 1.5
        assert 2.0 <= jittered.delay_for_attempt(2) <= 3.0


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: MaxTurnsExceeded("too many turns"),
        lambda: UserError("bad SDK usage"),
        _tripwire,
        lambda: _status_error(openai.AuthenticationError, 401),
    ],
)
async def test_non_transient_raises_immediately_and_skips_fallback(make_exc: Any) -> None:
    exc = make_exc()
    agent = FakeAgent()
    runner_fn, calls = make_runner([exc])
    sleep, delays = make_sleeper()

    with pytest.raises(type(exc)):
        await run_with_resilience(
            agent,
            "hi",
            policy=_policy(),
            fallback_model="gpt-fallback",
            runner_fn=runner_fn,
            sleep=sleep,
        )

    assert len(calls) == 1
    assert delays == []
    assert agent.clone_calls == []


async def test_fallback_uses_cloned_agent() -> None:
    agent = FakeAgent()
    errors = [_status_error(openai.InternalServerError, 500) for _ in range(2)]
    runner_fn, calls = make_runner([*errors, "fallback-ok"])
    sleep, delays = make_sleeper()

    result = await run_with_resilience(
        agent,
        "hi",
        policy=_policy(max_attempts=2),
        fallback_model="gpt-fallback",
        runner_fn=runner_fn,
        sleep=sleep,
    )

    assert result == "fallback-ok"
    assert agent.clone_calls == [{"model": "gpt-fallback"}]
    assert calls[0][0] is agent and calls[1][0] is agent
    assert calls[2][0] is not agent
    assert calls[2][0].model == "gpt-fallback"
    assert delays == [1.0]  # only between the two primary attempts


async def test_exhaustion_reraises_last_error() -> None:
    first = _status_error(openai.RateLimitError, 429, "first")
    last = _status_error(openai.InternalServerError, 500, "last")
    runner_fn, calls = make_runner([first, last])
    sleep, _ = make_sleeper()

    with pytest.raises(openai.InternalServerError) as excinfo:
        await run_with_resilience(
            FakeAgent(), "hi", policy=_policy(max_attempts=2), runner_fn=runner_fn, sleep=sleep
        )

    assert excinfo.value is last
    assert len(calls) == 2


async def test_exhaustion_after_fallback_reraises_last_fallback_error() -> None:
    errors = [_status_error(openai.RateLimitError, 429, f"e{i}") for i in range(4)]
    runner_fn, calls = make_runner(list(errors))
    sleep, _ = make_sleeper()

    with pytest.raises(openai.RateLimitError) as excinfo:
        await run_with_resilience(
            FakeAgent(),
            "hi",
            policy=_policy(max_attempts=2),
            fallback_model="gpt-fallback",
            runner_fn=runner_fn,
            sleep=sleep,
        )

    assert excinfo.value is errors[3]
    assert len(calls) == 4


async def test_runner_receives_run_config_session_and_max_turns() -> None:
    run_config, session = object(), object()
    runner_fn, calls = make_runner(["ok"])

    await run_with_resilience(
        FakeAgent(),
        "the input",
        run_config=run_config,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
        max_turns=7,
        policy=_policy(),
        runner_fn=runner_fn,
    )

    _, input_text, kwargs = calls[0]
    assert input_text == "the input"
    assert kwargs == {"max_turns": 7, "run_config": run_config, "session": session}


# ---- is_transient classification ----


@pytest.mark.parametrize(
    ("make_exc", "expected"),
    [
        (lambda: _status_error(openai.RateLimitError, 429), True),
        (lambda: openai.APITimeoutError(request=_REQUEST), True),
        (lambda: openai.APIConnectionError(request=_REQUEST), True),
        (lambda: _status_error(openai.InternalServerError, 500), True),
        (lambda: _status_error(openai.APIStatusError, 503), True),
        (lambda: _status_error(openai.APIStatusError, 418), False),
        (lambda: _status_error(openai.AuthenticationError, 401), False),
        (lambda: _status_error(openai.PermissionDeniedError, 403), False),
        (lambda: _status_error(openai.BadRequestError, 400), False),
        (lambda: ModelBehaviorError("malformed JSON"), True),
        (lambda: MaxTurnsExceeded("too many turns"), False),
        (lambda: UserError("misuse"), False),
        (_tripwire, False),
        (lambda: ValueError("unknown"), False),
    ],
)
def test_is_transient_classification(make_exc: Any, expected: bool) -> None:
    assert is_transient(make_exc()) is expected


# ---- env helpers ----


def test_retry_policy_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_MAX_ATTEMPTS", "LLM_RETRY_BASE_DELAY", "LLM_RETRY_MAX_DELAY"):
        monkeypatch.delenv(name, raising=False)
    defaults = RetryPolicy.from_env()
    assert (defaults.max_attempts, defaults.base_delay_seconds) == (3, 1.0)
    assert (defaults.max_delay_seconds, defaults.jitter) == (30.0, True)

    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("LLM_RETRY_BASE_DELAY", "0.5")
    monkeypatch.setenv("LLM_RETRY_MAX_DELAY", "12")
    policy = RetryPolicy.from_env()
    assert (policy.max_attempts, policy.base_delay_seconds) == (5, 0.5)
    assert policy.max_delay_seconds == 12.0


def test_fallback_and_concurrency_env_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_FALLBACK_MODEL", raising=False)
    assert fallback_model_from_env() is None
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "  ")
    assert fallback_model_from_env() is None
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "gpt-4.1-mini")
    assert fallback_model_from_env() == "gpt-4.1-mini"

    monkeypatch.delenv("MAX_CONCURRENT_STEPS", raising=False)
    assert max_concurrent_steps_from_env() == 0
    monkeypatch.setenv("MAX_CONCURRENT_STEPS", "4")
    assert max_concurrent_steps_from_env() == 4


# ---- gather_with_concurrency ----


async def test_gather_bounds_concurrency_and_preserves_order() -> None:
    current = high_water = 0

    async def worker(i: int) -> int:
        nonlocal current, high_water
        current += 1
        high_water = max(high_water, current)
        await asyncio.sleep(0.01)
        current -= 1
        return i

    results = await gather_with_concurrency(2, *(worker(i) for i in range(6)))

    assert results == list(range(6))
    assert high_water <= 2


async def test_gather_unlimited_when_limit_is_zero() -> None:
    started = 0
    release = asyncio.Event()

    async def worker(i: int) -> int:
        nonlocal started
        started += 1
        if started == 5:
            release.set()  # only reachable if all five run concurrently
        await release.wait()
        return i

    results = await asyncio.wait_for(
        gather_with_concurrency(0, *(worker(i) for i in range(5))), timeout=2
    )

    assert results == list(range(5))


async def test_gather_return_exceptions_passthrough() -> None:
    error = ValueError("bad step")

    async def ok(i: int) -> int:
        return i

    async def boom() -> None:
        raise error

    results = await gather_with_concurrency(2, ok(0), boom(), ok(2), return_exceptions=True)

    assert results[0] == 0
    assert results[1] is error
    assert results[2] == 2

    with pytest.raises(ValueError, match="bad step"):
        await gather_with_concurrency(2, ok(0), boom(), return_exceptions=False)
