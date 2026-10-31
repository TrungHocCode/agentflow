"""The stale-run watchdog interrupts abandoned queued/running runs only."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from test_support import use_test_adapters

from app.modules.runs.events import DiscardingRunEventPublisher
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService
from app.shared.events import ExecutionEvent

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


class _StaleRunRepository:
    def __init__(self, documents: list[RunDocument]) -> None:
        self.documents = documents
        self.saved: list[RunDocument] = []
        self.events: list[ExecutionEvent] = []

    async def list_stale_active(self, updated_before: datetime) -> list[RunDocument]:
        return [
            document
            for document in self.documents
            if document.status in {"queued", "running"} and document.updated_at < updated_before
        ]

    async def save(self, document: RunDocument) -> None:
        self.saved.append(document.model_copy(deep=True))

    async def append_event(self, event: ExecutionEvent) -> ExecutionEvent:
        self.events.append(event)
        return event


class TestStaleRunJanitor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        use_test_adapters(self)
        self.stale_queued = RunDocument(
            run_id="stale-queued",
            flow_id="flow",
            status="queued",
            updated_at=NOW - timedelta(hours=3),
        )
        self.stale_running = RunDocument(
            run_id="stale-running",
            flow_id="flow",
            status="running",
            updated_at=NOW - timedelta(hours=4),
        )
        self.fresh = RunDocument(
            run_id="fresh-running",
            flow_id="flow",
            status="running",
            updated_at=NOW - timedelta(minutes=20),
        )
        self.terminal = RunDocument(
            run_id="old-completed",
            flow_id="flow",
            status="completed",
            updated_at=NOW - timedelta(days=2),
        )
        self.repository = _StaleRunRepository([
            self.stale_queued,
            self.stale_running,
            self.fresh,
            self.terminal,
        ])
        self.service = RunService(
            run_repository=self.repository,
            workflow_repository=None,
            execution_port=AsyncMock(),
            event_publisher=DiscardingRunEventPublisher(),
        )

    async def test_only_stale_active_runs_are_interrupted(self) -> None:
        interrupted = await self.service.mark_stale_runs_zombie(NOW)

        self.assertEqual(
            {document.run_id for document in interrupted},
            {"stale-queued", "stale-running"},
        )
        self.assertEqual(
            {document.run_id for document in self.repository.saved},
            {"stale-queued", "stale-running"},
        )
        for document in self.repository.saved:
            self.assertEqual(document.status, "interrupted")
            self.assertEqual(document.error_code, "run_stale_interrupted")
        self.assertEqual({event.type for event in self.repository.events}, {"run_interrupted"})
        self.assertEqual({event.status for event in self.repository.events}, {"interrupted"})
        self.assertEqual(self.fresh.status, "running")
        self.assertIsNone(self.fresh.error_code)
        self.assertEqual(self.terminal.status, "completed")


if __name__ == "__main__":
    unittest.main()
