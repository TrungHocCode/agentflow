"""Ports exposed by workflow management."""

from typing import Any, Dict, List, Protocol

from app.modules.workflows.domain import WorkflowRecord


class WorkflowRepository(Protocol):
    async def create(
        self,
        name: str,
        description: str | None,
        user_id: str,
        definition: Dict[str, Any],
    ) -> WorkflowRecord:
        ...
    async def list(self, user_id: str) -> List[WorkflowRecord]:
        ...

    async def get(self, workflow_id: str, user_id: str) -> WorkflowRecord | None:
        ...

    async def get_definition(self, workflow_id: str, user_id: str = "default_user") -> Dict[str, Any] | None:
        ...
