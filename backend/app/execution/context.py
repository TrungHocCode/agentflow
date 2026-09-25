"""Ephemeral runtime context passed into LangGraph nodes."""

from collections.abc import Awaitable, Callable
from typing import TypedDict


class ExecutionContext(TypedDict, total=False):
    """Non-persisted capabilities available during a graph invocation."""

    on_assistant_token: Callable[[str], Awaitable[None]]
