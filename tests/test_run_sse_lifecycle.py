import asyncio
import os
import sys
import unittest
from typing import Any, AsyncGenerator, Dict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.ports import ExecutionPort
from app.execution.state import State
from app.infrastructure.redis.event_publisher import InMemoryRunEventPublisher
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService
from app.shared.events import ExecutionEvent


class _RunRepository:
    def __init__(self, document: RunDocument) -> None:
        self.document = document

    async def get(self, run_id: str) -> RunDocument | None:
        return self.document if self.document.run_id == run_id else None

    async def list_events(
        self,
        run_id: str,
        after_event_id: str | None = None,
        limit: int = 200,
    ) -> list[ExecutionEvent]:
        return []


class _ExecutionPort(ExecutionPort):
    async def create_plan(self, run_id: str, initial_state: State) -> State:
        raise NotImplementedError

    async def continue_conversation(
        self,
        run_id: str,
        message: str,
        metadata: Dict[str, Any] | None = None,
    ) -> State:
        raise NotImplementedError

    def execute_run(
        self,
        run_id: str,
        initial_state: State,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        raise NotImplementedError

    def stream_execution(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        raise NotImplementedError


class TestRunSSELifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_idle_stream_survives_timeout_until_terminal_event(self) -> None:
        run_id = "run-sse-timeout"
        repository = _RunRepository(
            RunDocument(run_id=run_id, flow_id="flow-1", user_id="user-1", status="running")
        )
        publisher = InMemoryRunEventPublisher()
        service = RunService(
            run_repository=repository,
            workflow_repository=None,
            execution_port=_ExecutionPort(),
            event_publisher=publisher,
        )
        stream = service.stream_run_events(run_id)

        first_frame = await anext(stream)
        self.assertIn('"legacy_type": "start"', first_frame)

        async def publish_terminal_event() -> None:
            await asyncio.sleep(1.1)
            await publisher.publish(
                ExecutionEvent(
                    run_id=run_id,
                    type="run_completed",
                    phase="execute",
                    status="completed",
                )
            )

        publisher_task = asyncio.create_task(publish_terminal_event())
        try:
            terminal_frame = await anext(stream)
        finally:
            await publisher_task
            await stream.aclose()

        self.assertIn('"type": "run_completed"', terminal_frame)


if __name__ == "__main__":
    unittest.main()
