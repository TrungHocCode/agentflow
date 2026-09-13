"""SQLAlchemy adapter for workflow persistence."""

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.flows.models import FlowModel
from app.modules.workflows.domain import WorkflowRecord
from app.modules.workflows.ports import WorkflowRepository


class PostgresWorkflowRepository(WorkflowRepository):
    """Maps FlowModel rows to workflow domain records."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        name: str,
        description: str | None,
        user_id: str,
        definition: Dict[str, Any],
    ) -> WorkflowRecord:
        workflow = FlowModel(
            name=name,
            description=description,
            user_id=user_id,
            definition=definition,
        )
        self.session.add(workflow)
        await self.session.commit()
        await self.session.refresh(workflow)
        return self._to_domain(workflow)

    async def list(self, user_id: str) -> List[WorkflowRecord]:
        try:
            result = await self.session.execute(
                select(FlowModel)
                .where(FlowModel.user_id == user_id)
                .order_by(FlowModel.created_at.desc())
            )
        except Exception:
            return []
        return [self._to_domain(row) for row in result.scalars().all()]

    async def get(self, workflow_id: str, user_id: str) -> WorkflowRecord | None:
        try:
            result = await self.session.execute(
                select(FlowModel).where(
                    FlowModel.id == workflow_id,
                    FlowModel.user_id == user_id,
                )
            )
        except Exception:
            return None
        workflow = result.scalar_one_or_none()
        return self._to_domain(workflow) if workflow else None

    async def get_definition(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> Dict[str, Any] | None:
        try:
            result = await self.session.execute(
                select(FlowModel.definition).where(
                    FlowModel.id == workflow_id,
                    FlowModel.user_id == user_id,
                )
            )
        except Exception:
            return None
        definition = result.scalar_one_or_none()
        return definition if definition is not None else None

    @staticmethod
    def _to_domain(workflow: FlowModel) -> WorkflowRecord:
        created_at = workflow.created_at or datetime.now(timezone.utc)
        updated_at = workflow.updated_at or created_at
        return WorkflowRecord(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            user_id=workflow.user_id,
            definition=workflow.definition or {},
            created_at=created_at,
            updated_at=updated_at,
        )
