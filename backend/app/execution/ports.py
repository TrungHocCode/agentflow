"""Execution subsystem ports consumed by the Runs module."""

from typing import Any, AsyncGenerator, Dict, Protocol

from app.execution.state import State


class ExecutionPort(Protocol):
    async def create_plan(self, run_id: str, initial_state: State) -> State:
        ...
    async def continue_conversation(self, run_id: str, message: str) -> State:
        ...

    def stream_execution(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        ...
