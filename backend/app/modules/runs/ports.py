"""Ports owned by the Runs bounded context."""

from typing import List, Protocol

from app.modules.runs.models import RunDocument


class RunRepository(Protocol):
    async def save(self, document: RunDocument) -> None:
        ...
    async def get(self, run_id: str) -> RunDocument | None:
        ...

    async def list(self, flow_id: str | None = None, limit: int = 50) -> List[RunDocument]:
        ...
