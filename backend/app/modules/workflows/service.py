"""Application service for workflow management."""

from typing import List

from app.execution.state import FlowDefinition
from app.modules.workflows.domain import WorkflowRecord, WorkflowVersionRecord
from app.modules.workflows.ports import WorkflowRepository
from app.modules.workflows.schemas import WorkflowCreateRequest, WorkflowUpdateRequest
from app.modules.workflows.validator import validate_workflow_definition


class WorkflowService:
    """Owns workflow use cases while delegating persistence to a repository port."""

    def __init__(self, repository: WorkflowRepository):
        self.repository = repository

    async def create_workflow(
        self,
        request: WorkflowCreateRequest,
        user_id: str = "default_user",
    ) -> WorkflowRecord:
        validate_workflow_definition(request.definition)
        definition = request.definition.model_dump() if request.definition else {}
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
            request.definition.model_dump()
            if request.definition is not None
            else current.definition
        )
        if request.definition is not None:
            validate_workflow_definition(request.definition)
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
        definition: FlowDefinition,
        user_id: str = "default_user",
    ) -> WorkflowVersionRecord | None:
        validate_workflow_definition(definition)
        method = getattr(self.repository, "create_version", None)
        if method is None:
            return None
        return await method(workflow_id, user_id, definition.model_dump(mode="json"))

    async def publish_version(
        self,
        workflow_id: str,
        version_id: str,
        user_id: str = "default_user",
    ) -> WorkflowVersionRecord | None:
        method = getattr(self.repository, "publish_version", None)
        return await method(workflow_id, version_id, user_id) if method else None
