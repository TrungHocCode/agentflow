"""SQLAlchemy adapter for workflow persistence."""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.models import FlowModel, WorkflowVersionModel
from app.modules.workflows.domain import WorkflowRecord
from app.modules.workflows.ports import WorkflowRepository
from app.shared.errors import PersistenceError


_IN_MEMORY_WORKFLOWS: Dict[str, WorkflowRecord] = {}


class PostgresWorkflowRepository(WorkflowRepository):
    """Maps PostgreSQL rows to workflow domain records."""

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
        try:
            workflow = FlowModel(
                name=name,
                description=description,
                user_id=user_id,
                definition=definition,
            )
            self.session.add(workflow)
            await self.session.flush()
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
            await self.session.refresh(workflow)
            record = self._to_domain(workflow)
            record.version_id = version_id
            record.version_number = 1
            return record
        except Exception as exc:
            await self.session.rollback()
            raise PersistenceError("Could not create workflow transactionally.") from exc

    async def update(
        self,
        workflow_id: str,
        user_id: str,
        name: str,
        description: str | None,
        definition: Dict[str, Any],
    ) -> WorkflowRecord | None:
        """Update the aggregate and append a new immutable version atomically."""

        if self.use_memory:
            current = _IN_MEMORY_WORKFLOWS.get(workflow_id)
            if current is None or current.user_id != user_id or current.status == "archived":
                return None
            version_number = (current.version_number or 0) + 1
            now = datetime.now(timezone.utc)
            record = current.model_copy(
                update={
                    "name": name,
                    "description": description,
                    "definition": definition,
                    "status": "active",
                    "version_id": str(uuid.uuid4()),
                    "version_number": version_number,
                    "updated_at": now,
                }
            )
            _IN_MEMORY_WORKFLOWS[workflow_id] = record
            return record

        try:
            result = await self.session.execute(
                select(FlowModel)
                .where(
                    FlowModel.id == workflow_id,
                    FlowModel.user_id == user_id,
                )
                .with_for_update()
            )
            workflow = result.scalar_one_or_none()
            if workflow is None or workflow.status == "archived":
                return None

            maximum = await self.session.scalar(
                select(func.max(WorkflowVersionModel.version_number)).where(
                    WorkflowVersionModel.workflow_id == workflow_id
                )
            )
            version_number = (maximum or 0) + 1
            version_id = str(uuid.uuid4())
            workflow.name = name
            workflow.description = description
            workflow.definition = definition
            workflow.status = "active"
            self.session.add(
                WorkflowVersionModel(
                    id=version_id,
                    workflow_id=workflow_id,
                    version_number=version_number,
                    definition=definition,
                    created_by=user_id,
                )
            )
            await self.session.commit()
            await self.session.refresh(workflow)
            record = self._to_domain(workflow)
            record.version_id = version_id
            record.version_number = version_number
            return record
        except Exception as exc:
            await self.session.rollback()
            raise PersistenceError("Could not update workflow transactionally.") from exc

    async def archive(self, workflow_id: str, user_id: str) -> WorkflowRecord | None:
        """Archive a workflow without deleting versions or historical runs."""

        if self.use_memory:
            current = _IN_MEMORY_WORKFLOWS.get(workflow_id)
            if current is None or current.user_id != user_id:
                return None
            record = current.model_copy(
                update={
                    "status": "archived",
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            _IN_MEMORY_WORKFLOWS[workflow_id] = record
            return record

        try:
            result = await self.session.execute(
                select(FlowModel)
                .where(
                    FlowModel.id == workflow_id,
                    FlowModel.user_id == user_id,
                )
                .with_for_update()
            )
            workflow = result.scalar_one_or_none()
            if workflow is None:
                return None
            workflow.status = "archived"
            await self.session.commit()
            await self.session.refresh(workflow)
            return self._to_domain(workflow)
        except Exception as exc:
            await self.session.rollback()
            raise PersistenceError("Could not archive workflow.") from exc

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
        except Exception as exc:
            raise PersistenceError("Could not list workflows.") from exc
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
        except Exception as exc:
            raise PersistenceError("Could not load workflow.") from exc
        workflow = result.scalar_one_or_none()
        if workflow is None:
            return None
        record = self._to_domain(workflow)
        if workflow.status == "active":
            current_version = await self.get_current_version(workflow_id, user_id)
            if current_version is not None:
                record.version_id = current_version.version_id
                record.version_number = current_version.version_number
                record.definition = current_version.definition
        return record

    async def get_definition(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> Dict[str, Any] | None:
        if self.use_memory:
            record = await self.get(workflow_id, user_id)
            return (
                record.definition
                if record and record.status == "active"
                else None
            )
        current_version = await self.get_current_version(workflow_id, user_id)
        if current_version:
            return current_version.definition
        try:
            result = await self.session.execute(
                select(FlowModel.definition).where(
                    FlowModel.id == workflow_id,
                    FlowModel.user_id == user_id,
                    FlowModel.status == "active",
                )
            )
        except Exception as exc:
            raise PersistenceError("Could not load workflow definition.") from exc
        definition = result.scalar_one_or_none()
        return definition if definition is not None else None

    async def get_current_version(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> WorkflowRecord | None:
        if self.use_memory:
            record = await self.get(workflow_id, user_id)
            return record if record and record.status == "active" else None
        try:
            result = await self.session.execute(
                select(WorkflowVersionModel, FlowModel)
                .join(FlowModel, FlowModel.id == WorkflowVersionModel.workflow_id)
                .where(
                    WorkflowVersionModel.workflow_id == workflow_id,
                    WorkflowVersionModel.status == "published",
                    FlowModel.user_id == user_id,
                    FlowModel.status == "active",
                )
                .order_by(WorkflowVersionModel.version_number.desc())
                .limit(1)
            )
        except Exception as exc:
            raise PersistenceError("Could not load current workflow version.") from exc
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
            status=workflow.status,
            definition=workflow.definition or {},
            created_at=created_at,
            updated_at=updated_at,
        )
