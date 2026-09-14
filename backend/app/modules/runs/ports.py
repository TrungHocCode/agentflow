"""Ports owned by the Runs bounded context."""

from typing import List, Protocol

from app.modules.runs.models import RunDocument
from app.shared.events import ExecutionEvent


class RunRepository(Protocol):
    async def save(self, document: RunDocument) -> None:
        ...
    async def get(self, run_id: str) -> RunDocument | None:
        ...

    async def list(self, flow_id: str | None = None, limit: int = 50) -> List[RunDocument]:
        ...

    async def find_by_idempotency_key(self, idempotency_key: str) -> RunDocument | None:
        ...

    async def claim(self, run_id: str) -> RunDocument | None:
        """Atomically claim a queued run for one worker."""
        ...

    async def append_event(self, event: ExecutionEvent) -> ExecutionEvent:
        ...

    async def list_events(
        self,
        run_id: str,
        after_event_id: str | None = None,
        limit: int = 200,
    ) -> List[ExecutionEvent]:
        ...
