"""Application service for workflow management."""

from typing import Any, Dict, List

from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.workflows.domain import WorkflowRecord, WorkflowVersionRecord
from app.modules.workflows.ports import WorkflowRepository
from app.modules.workflows.schemas import WorkflowCreateRequest, WorkflowUpdateRequest
from app.modules.workflows.validator import (
    validate_workflow_definition,
    validate_workflow_references,
)
from app.modules.catalog.ports import CatalogRepository


class WorkflowService:
    """Owns workflow use cases while delegating persistence to a repository port."""

    def __init__(
        self,
        repository: WorkflowRepository,
        catalog_repository: CatalogRepository | None = None,
    ):
        self.repository = repository
        self.catalog_repository = catalog_repository

    async def create_workflow(
        self,
        request: WorkflowCreateRequest,
        user_id: str = "default_user",
    ) -> WorkflowRecord:
        definition = await self.validate_definition(request.definition)
        return await self.repository.create(
            name=request.name,
            description=request.description,
            user_id=user_id,
            definition=definition,
        )

    async def list_workflows(self, user_id: str = "default_user") -> List[WorkflowRecord]:
        return await self.repository.list(user_id=user_id)

    async def update_workflow(
        self,
        workflow_id: str,
        request: WorkflowUpdateRequest,
        user_id: str = "default_user",
    ) -> WorkflowRecord | None:
        """Update metadata and create a new immutable workflow version."""

        current = await self.repository.get(workflow_id=workflow_id, user_id=user_id)
        if current is None or current.status == "archived":
            return None

        definition = (
            await self.validate_definition(request.definition)
            if request.definition is not None
            else current.definition
        )
        return await self.repository.update(
            workflow_id=workflow_id,
            user_id=user_id,
            name=request.name if request.name is not None else current.name,
            description=(
                request.description
                if request.description is not None
                else current.description
            ),
            definition=definition,
        )

    async def archive_workflow(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> WorkflowRecord | None:
        """Archive a workflow without deleting its historical versions or runs."""

        return await self.repository.archive(workflow_id=workflow_id, user_id=user_id)

    async def get_workflow(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> WorkflowRecord | None:
        return await self.repository.get(workflow_id=workflow_id, user_id=user_id)

    async def list_versions(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> List[WorkflowVersionRecord]:
        method = getattr(self.repository, "list_versions", None)
        return await method(workflow_id, user_id) if method else []

    async def get_version(
        self,
        workflow_id: str,
        version_id: str,
        user_id: str = "default_user",
    ) -> WorkflowVersionRecord | None:
        method = getattr(self.repository, "get_version", None)
        return await method(workflow_id, version_id, user_id) if method else None

    async def create_version(
        self,
        workflow_id: str,
        definition: Dict[str, Any],
        user_id: str = "default_user",
    ) -> WorkflowVersionRecord | None:
        normalized = await self.validate_definition(definition)
        method = getattr(self.repository, "create_version", None)
        if method is None:
            return None
        return await method(workflow_id, user_id, normalized)

    async def publish_version(
        self,
        workflow_id: str,
        version_id: str,
        user_id: str = "default_user",
    ) -> WorkflowVersionRecord | None:
        version = await self.get_version(workflow_id, version_id, user_id)
        if version is None:
            return None
        await self.validate_definition(version.definition, require_steps=True)
        method = getattr(self.repository, "publish_version", None)
        return await method(workflow_id, version_id, user_id) if method else None

    async def validate_definition(
        self,
        definition: Dict[str, Any] | None,
        *,
        require_steps: bool = False,
    ) -> Dict[str, Any]:
        normalized = normalize_workflow_definition(definition)
        validate_workflow_definition(normalized, require_steps=require_steps)
        resolved = await validate_workflow_references(normalized, self.catalog_repository)
        validate_workflow_definition(resolved, require_steps=require_steps)
        return resolved
