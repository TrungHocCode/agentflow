"""Ports owned by the Runs bounded context."""

from datetime import datetime
from typing import List, Protocol, Sequence

from app.modules.runs.models import RunDocument
from app.shared.events import ExecutionEvent


class RunRepository(Protocol):
    async def save(self, document: RunDocument) -> None:
        ...

    async def save_if_plan_revision_matches(
        self,
        document: RunDocument,
        expected_revision: str,
        expected_updated_at: datetime,
        allowed_statuses: Sequence[str],
    ) -> bool:
        """Atomically persist a transition against its revision and source snapshot."""
        ...
    async def get(self, run_id: str, user_id: str | None = None) -> RunDocument | None:
        ...

    async def list(
        self,
        flow_id: str | None = None,
        limit: int = 50,
        user_id: str | None = None,
    ) -> List[RunDocument]:
        ...

    async def find_by_idempotency_key(
        self,
        idempotency_key: str,
        user_id: str,
    ) -> RunDocument | None:
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
