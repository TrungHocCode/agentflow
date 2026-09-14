"""Ports exposed by workflow management."""

from typing import Any, Dict, List, Protocol

from app.modules.workflows.domain import WorkflowRecord, WorkflowVersionRecord


class WorkflowRepository(Protocol):
    async def create(
        self,
        name: str,
        description: str | None,
        user_id: str,
        definition: Dict[str, Any],
    ) -> WorkflowRecord:
        ...

    async def update(
        self,
        workflow_id: str,
        user_id: str,
        name: str,
        description: str | None,
        definition: Dict[str, Any],
    ) -> WorkflowRecord | None:
        ...

    async def archive(self, workflow_id: str, user_id: str) -> WorkflowRecord | None:
        ...

    async def list(self, user_id: str) -> List[WorkflowRecord]:
        ...

    async def get(self, workflow_id: str, user_id: str) -> WorkflowRecord | None:
        ...

    async def get_definition(self, workflow_id: str, user_id: str = "default_user") -> Dict[str, Any] | None:
        ...

    async def get_current_version(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> WorkflowRecord | None:
        ...

    async def list_versions(self, workflow_id: str, user_id: str = "default_user") -> List[WorkflowVersionRecord]:
        ...

    async def get_version(
        self,
        workflow_id: str,
        version_id: str,
        user_id: str = "default_user",
    ) -> WorkflowVersionRecord | None:
        ...

    async def create_version(
        self,
        workflow_id: str,
        user_id: str,
        definition: Dict[str, Any],
    ) -> WorkflowVersionRecord | None:
        ...

    async def publish_version(
        self,
        workflow_id: str,
        version_id: str,
        user_id: str,
    ) -> WorkflowVersionRecord | None:
        ...
