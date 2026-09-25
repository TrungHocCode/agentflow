"""Execution subsystem ports consumed by the Runs module."""

from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, Protocol

from app.execution.state import State


AssistantTokenCallback = Callable[[str], Awaitable[None]]


class ExecutionPort(Protocol):
    async def create_plan(
        self,
        run_id: str,
        initial_state: State,
        on_assistant_token: AssistantTokenCallback | None = None,
    ) -> State:
        ...

    async def continue_conversation(
        self,
        run_id: str,
        message: str,
        metadata: Dict[str, Any] | None = None,
        on_assistant_token: AssistantTokenCallback | None = None,
    ) -> State:
        ...

    def execute_run(self, run_id: str, initial_state: State) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute a durable run from its persisted state."""
        ...

    def stream_execution(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        ...
