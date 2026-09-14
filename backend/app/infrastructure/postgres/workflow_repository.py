"""SQLAlchemy adapter for workflow persistence."""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.flows.models import FlowModel
from app.modules.workflows.domain import WorkflowRecord
from app.modules.workflows.orm import WorkflowVersionModel
from app.modules.workflows.ports import WorkflowRepository


_IN_MEMORY_WORKFLOWS: Dict[str, WorkflowRecord] = {}


class PostgresWorkflowRepository(WorkflowRepository):
    """Maps FlowModel rows to workflow domain records."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @property
    def use_memory(self) -> bool:
        return os.getenv("TESTING", "").lower() == "true"

    async def create(
        self,
        name: str,
        description: str | None,
        user_id: str,
        definition: Dict[str, Any],
    ) -> WorkflowRecord:
        if self.use_memory:
            now = datetime.now(timezone.utc)
            workflow_id = str(uuid.uuid4())
            record = WorkflowRecord(
                id=workflow_id,
                version_id=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"agentflow:{workflow_id}:v1")
                ),
                version_number=1,
                name=name,
                description=description,
                user_id=user_id,
                definition=definition,
                created_at=now,
                updated_at=now,
            )
            _IN_MEMORY_WORKFLOWS[workflow_id] = record
            return record
        workflow = FlowModel(
            name=name,
            description=description,
            user_id=user_id,
            definition=definition,
        )
        self.session.add(workflow)
        await self.session.commit()
        await self.session.refresh(workflow)
        version_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"agentflow:{workflow.id}:v1")
        )
        self.session.add(
            WorkflowVersionModel(
                id=version_id,
                workflow_id=workflow.id,
                version_number=1,
                definition=definition,
                created_by=user_id,
            )
        )
        await self.session.commit()
        record = self._to_domain(workflow)
        record.version_id = version_id
        record.version_number = 1
        return record

    async def list(self, user_id: str) -> List[WorkflowRecord]:
        if self.use_memory:
            return [
                record
                for record in _IN_MEMORY_WORKFLOWS.values()
                if record.user_id == user_id
            ]
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
        if self.use_memory:
            record = _IN_MEMORY_WORKFLOWS.get(workflow_id)
            return record if record and record.user_id == user_id else None
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
        if self.use_memory:
            record = await self.get(workflow_id, user_id)
            return record.definition if record else None
        current_version = await self.get_current_version(workflow_id, user_id)
        if current_version:
            return current_version.definition
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

    async def get_current_version(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> WorkflowRecord | None:
        if self.use_memory:
            return await self.get(workflow_id, user_id)
        try:
            result = await self.session.execute(
                select(WorkflowVersionModel, FlowModel)
                .join(FlowModel, FlowModel.id == WorkflowVersionModel.workflow_id)
                .where(
                    WorkflowVersionModel.workflow_id == workflow_id,
                    FlowModel.user_id == user_id,
                )
                .order_by(WorkflowVersionModel.version_number.desc())
                .limit(1)
            )
        except Exception:
            return None
        row = result.first()
        if not row:
            return None
        version, workflow = row
        record = self._to_domain(workflow)
        record.version_id = version.id
        record.version_number = version.version_number
        record.definition = version.definition or {}
        return record

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
