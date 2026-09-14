"""Realtime event publisher port."""

from typing import AsyncIterator, Protocol

from app.shared.events import ExecutionEvent


class RunEventPublisher(Protocol):
    async def publish(self, event: ExecutionEvent) -> None:
        ...

    def subscribe(self, run_id: str) -> AsyncIterator[ExecutionEvent]:
        ...


class DiscardingRunEventPublisher:
    """Compatibility publisher for direct unit construction without composition."""

    async def publish(self, event: ExecutionEvent) -> None:
        return None

    async def _empty(self) -> AsyncIterator[ExecutionEvent]:
        if False:
            yield ExecutionEvent(run_id="", type="noop")

    def subscribe(self, run_id: str) -> AsyncIterator[ExecutionEvent]:
        return self._empty()
