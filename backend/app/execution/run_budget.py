"""Run-cumulative admission budgets and cooperative cancellation for nested operations."""

import asyncio
import json
import math
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Any, AsyncGenerator

from app.core.config import MAX_ACTIVE_CHECK_INTERVAL_SECONDS, settings
from app.execution.context_budget import estimate_tokens, request_estimate


__all__ = [
    "MAX_ACTIVE_CHECK_INTERVAL_SECONDS",
    "RunBudget",
    "RunBudgetExceeded",
    "RunNoLongerActive",
    "bounded_invoke",
    "bounded_operation",
    "bounded_stream",
    "current_budget",
    "run_budget_scope",
]


class RunBudgetExceeded(ValueError):
    code = "run_budget_exhausted"


class RunNoLongerActive(ValueError):
    code = "run_cancelled"


class RunBudget:
    """Charge conservative estimated input + reserved output, including failed inference calls.

    This is an admission estimate, not provider token usage. No reservation is refunded after
    cancellation/failure; accepted runs are not automatically replayed after process loss.

    The optional ``check_active`` callback is throttled by ``active_check_interval_seconds``
    so long-running bounded operations can still detect cancellation without re-reading run
    status from PostgreSQL on every poll tick. The interval is capped at
    ``MAX_ACTIVE_CHECK_INTERVAL_SECONDS`` because cooperative cancellation is detected within
    the interval plus one 0.25s poll tick.
    """

    def __init__(self, max_calls: int, max_tokens: int, max_seconds: float,
                 check_active: Callable[[], Awaitable[None]] | None = None,
                 active_check_interval_seconds: float = 0.0) -> None:
        if (not math.isfinite(active_check_interval_seconds)
                or not 0.0 <= active_check_interval_seconds <= MAX_ACTIVE_CHECK_INTERVAL_SECONDS):
            raise ValueError(
                "active_check_interval_seconds must be a finite value between 0 and "
                f"{MAX_ACTIVE_CHECK_INTERVAL_SECONDS} seconds."
            )
        self.max_calls, self.max_tokens, self.max_seconds = max_calls, max_tokens, max_seconds
        self.check_active = check_active
        self.active_check_interval_seconds = active_check_interval_seconds
        self._last_active_check: float | None = None
        self.started = monotonic()
        self.calls = 0
        self.reserved_tokens = 0
        self.claimed = False

    def reserve(self, estimated_tokens: int = 0, inference: bool = False) -> None:
        if estimated_tokens < 0:
            raise ValueError("Token reservations must not be negative.")
        if monotonic() - self.started >= self.max_seconds:
            raise RunBudgetExceeded("Run wall-clock budget exhausted.")
        if inference:
            if self.calls >= self.max_calls or self.reserved_tokens + estimated_tokens > self.max_tokens:
                raise RunBudgetExceeded("Run inference call/token admission budget exhausted.")
            self.calls += 1
            self.reserved_tokens += estimated_tokens

    async def ensure_active(self) -> None:
        """Await the cancellation callback, throttled to the configured interval."""
        if self.check_active is None:
            return
        now = monotonic()
        if (
            self._last_active_check is not None
            and now - self._last_active_check < self.active_check_interval_seconds
        ):
            return
        self._last_active_check = now
        await self.check_active()

    def snapshot(self) -> dict[str, Any]:
        return {"calls": self.calls, "reserved_estimated_tokens": self.reserved_tokens,
                "max_calls": self.max_calls, "max_estimated_tokens": self.max_tokens,
                "max_seconds": self.max_seconds, "elapsed_seconds": round(monotonic() - self.started, 3),
                "counting_method": "utf8/2_input_plus_reserved_output_not_provider_usage"}


_CURRENT: ContextVar[RunBudget | None] = ContextVar("run_budget", default=None)


@contextmanager
def run_budget_scope(budget: RunBudget) -> Iterator[RunBudget]:
    token = _CURRENT.set(budget)
    try:
        yield budget
    finally:
        _CURRENT.reset(token)


def current_budget() -> RunBudget | None:
    return _CURRENT.get()


async def bounded_operation(factory: Callable[[], Awaitable[Any]], estimated_tokens: int = 0,
                            inference: bool = False) -> Any:
    budget = current_budget()
    if budget is None:
        return await factory()
    await budget.ensure_active()
    budget.reserve(estimated_tokens, inference)
    operation = asyncio.ensure_future(factory())
    try:
        while True:
            remaining = budget.max_seconds - (monotonic() - budget.started)
            if remaining <= 0:
                raise RunBudgetExceeded("Run wall-clock budget exhausted.")
            done, _ = await asyncio.wait({operation}, timeout=min(remaining, 0.25))
            await budget.ensure_active()
            if done:
                return operation.result()
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def bounded_invoke(runnable: Any, messages: list, tools: list | None = None,
                         output_tokens: int | None = None, schema: dict | None = None, **kwargs: Any) -> Any:
    reservation = request_estimate(messages, tools or []) + (output_tokens or settings.LLM_OUTPUT_TOKENS)
    if schema is not None:
        reservation += estimate_tokens(json.dumps(schema, ensure_ascii=False))
    return await bounded_operation(lambda: runnable.ainvoke(messages, **kwargs), reservation, inference=True)


async def bounded_stream(stream: AsyncGenerator[dict, None]) -> AsyncGenerator[dict, None]:
    """Enforce deadline/cancellation even while waiting for an execution node update."""
    try:
        while True:
            try:
                chunk = await bounded_operation(stream.__anext__)
            except StopAsyncIteration:
                break
            yield chunk
    finally:
        await stream.aclose()
