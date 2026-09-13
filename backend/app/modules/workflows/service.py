"""Application service for workflow management."""

from typing import List

from app.modules.workflows.domain import WorkflowRecord
from app.modules.workflows.ports import WorkflowRepository
from app.modules.workflows.schemas import WorkflowCreateRequest
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

    async def get_workflow(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> WorkflowRecord | None:
        return await self.repository.get(workflow_id=workflow_id, user_id=user_id)
