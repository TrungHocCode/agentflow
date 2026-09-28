"""PostgreSQL adapter for durable run state and replayable events."""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Sequence, TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models import (
    RunEventModel,
    RunModel,
    TaskExecutionModel,
    WorkflowStepModel,
)
from app.modules.runs.models import RunDocument
from app.modules.runs.ports import RunRepository
from app.shared.errors import PersistenceError
from app.shared.events import ExecutionEvent


_T = TypeVar("_T")
_IN_MEMORY_RUNS: Dict[str, Dict[str, Any]] = {}
_IN_MEMORY_EVENTS: Dict[str, List[ExecutionEvent]] = {}


class PostgresRunRepository(RunRepository):
    """Persist run control data in PostgreSQL with a test fallback."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: Callable[[], AsyncSession] = AsyncSessionLocal,
    ) -> None:
        self.session = session
        self.session_factory = session_factory

    @property
    def use_memory(self) -> bool:
        return os.getenv("TESTING", "").lower() == "true"

    async def _with_session(
        self,
        operation: Callable[[AsyncSession], Awaitable[_T]],
    ) -> _T:
        if self.session is not None:
            return await operation(self.session)
        async with self.session_factory() as session:
            return await operation(session)

    async def save(self, document: RunDocument) -> None:
        if self.use_memory:
            self._save_memory(document)
            return

        try:
            async def operation(session: AsyncSession) -> None:
                record = await session.get(RunModel, document.run_id)
                values = self._to_orm_values(document)
                if record is None:
                    session.add(RunModel(**values))
                    await session.flush()
                    await self._create_task_execution_rows(session, document)
                else:
                    for key, value in values.items():
                        setattr(record, key, value)
                    await self._sync_task_execution_rows(session, document)
                await session.commit()

            await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not save run state.") from exc

    async def save_if_plan_revision_matches(
        self,
        document: RunDocument,
        expected_revision: str,
        expected_updated_at: datetime,
        allowed_statuses: Sequence[str],
    ) -> bool:
        """Persist only if neither the reviewed plan nor its run snapshot has changed."""

        if self.use_memory:
            current = _IN_MEMORY_RUNS.get(document.run_id)
            if (
                current is None
                or current.get("user_id") != document.user_id
                or current.get("plan_revision") != expected_revision
                or current.get("updated_at") != expected_updated_at
                or current.get("status") not in set(allowed_statuses)
            ):
                return False
            self._save_memory(document)
            return True

        try:
            async def operation(session: AsyncSession) -> bool:
                result = await session.execute(
                    update(RunModel)
                    .where(
                        RunModel.run_id == document.run_id,
                        RunModel.user_id == document.user_id,
                        RunModel.plan_revision == expected_revision,
                        RunModel.updated_at == expected_updated_at,
                        RunModel.status.in_(list(allowed_statuses)),
                    )
                    .values(**self._to_orm_values(document))
                )
                if result.rowcount != 1:
                    await session.rollback()
                    return False
                await self._sync_task_execution_rows(session, document)
                await session.commit()
                return True

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not conditionally save the workflow plan.") from exc

    @staticmethod
    async def _create_task_execution_rows(
        session: AsyncSession,
        document: RunDocument,
    ) -> None:
        """Create the at-most-once task identities alongside a new run row."""

        if not document.workflow_version_id:
            return
        task_keys = [task.task_key for task in document.plan if task.task_key]
        if not task_keys:
            return
        result = await session.execute(
            select(WorkflowStepModel).where(
                WorkflowStepModel.workflow_version_id == document.workflow_version_id,
                WorkflowStepModel.task_key.in_(task_keys),
            )
        )
        step_by_key = {step.task_key: step for step in result.scalars().all()}
        missing = set(task_keys) - set(step_by_key)
        if missing:
            raise ValueError(
                "The selected workflow version is missing normalized steps: "
                + ", ".join(sorted(missing))
            )

        session.add_all(
            [
                TaskExecutionModel(
                    id=task.task_execution_id
                    or str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"agentflow:{document.run_id}:task:{task.task_key}",
                        )
                    ),
                    run_id=document.run_id,
                    workflow_step_id=step_by_key[task.task_key].id,
                    task_key=task.task_key,
                    status="pending",
                    attempt_number=1,
                    agent_ref=task.agent_id or task.node,
                    resolved_tool_ids=list(task.tool_ids),
                    input_reference=task.input_mapping,
                )
                for task in document.plan
                if task.task_key
            ]
        )

    @staticmethod
    async def _sync_task_execution_rows(
        session: AsyncSession,
        document: RunDocument,
    ) -> None:
        """Keep normalized runtime status aligned with the durable run snapshot."""

        result = await session.execute(
            select(TaskExecutionModel).where(TaskExecutionModel.run_id == document.run_id)
        )
        rows = {row.task_key: row for row in result.scalars().all()}
        now = datetime.now(timezone.utc)
        for task in document.plan:
            if not task.task_key or task.task_key not in rows:
                continue
            row = rows[task.task_key]
            status = "completed" if task.status in {"done", "partial"} else task.status
            row.status = status
            row.agent_ref = task.agent_id or task.node
            row.resolved_tool_ids = list(task.tool_ids)
            row.input_reference = task.input_mapping
            row.error_message = task.error
            if status == "running" and row.started_at is None:
                row.started_at = now
            if status in {"completed", "failed", "skipped", "cancelled", "interrupted"}:
                row.completed_at = row.completed_at or now

    async def get(self, run_id: str, user_id: str | None = None) -> RunDocument | None:
        if self.use_memory:
            data = _IN_MEMORY_RUNS.get(run_id)
            if data and user_id is not None and data.get("user_id") != user_id:
                data = None
            return RunDocument(**data) if data else None
        if not self.use_memory:
            try:
                result = await self._with_session(
                    lambda session: session.execute(
                        select(RunModel).where(
                            RunModel.run_id == run_id,
                            *( [RunModel.user_id == user_id] if user_id is not None else [] ),
                        )
                    )
                )
                record = result.scalar_one_or_none()
                if record is not None:
                    return self._to_domain(record)
            except Exception as exc:
                await self._rollback()
                raise PersistenceError("Could not load run state.") from exc

        return None

    async def list(
        self,
        flow_id: str | None = None,
        limit: int = 50,
        user_id: str | None = None,
    ) -> List[RunDocument]:
        if self.use_memory:
            documents = [
                RunDocument(**data)
                for data in _IN_MEMORY_RUNS.values()
                if (flow_id is None or data.get("flow_id") == flow_id)
                and (user_id is None or data.get("user_id") == user_id)
            ]
            return documents[:limit]
        if not self.use_memory:
            try:
                statement = select(RunModel).order_by(RunModel.created_at.desc()).limit(limit)
                if flow_id:
                    statement = statement.where(RunModel.flow_id == flow_id)
                if user_id:
                    statement = statement.where(RunModel.user_id == user_id)
                result = await self._with_session(lambda session: session.execute(statement))
                records = result.scalars().all()
                if records:
                    return [self._to_domain(record) for record in records]
            except Exception as exc:
                await self._rollback()
                raise PersistenceError("Could not list run state.") from exc

        return []

    async def find_by_idempotency_key(
        self,
        idempotency_key: str,
        user_id: str,
    ) -> RunDocument | None:
        if not idempotency_key:
            return None
        if self.use_memory:
            for data in _IN_MEMORY_RUNS.values():
                if data.get("idempotency_key") == idempotency_key:
                    return RunDocument(**data)
            return None
        if not self.use_memory:
            try:
                result = await self._with_session(
                    lambda session: session.execute(
                        select(RunModel).where(
                            RunModel.idempotency_key == idempotency_key,
                            RunModel.user_id == user_id,
                        )
                    )
                )
                record = result.scalar_one_or_none()
                if record is not None:
                    return self._to_domain(record)
            except Exception as exc:
                await self._rollback()
                raise PersistenceError("Could not find run by idempotency key.") from exc

        return None

    async def claim(self, run_id: str) -> RunDocument | None:
        """Atomically transition queued -> running."""

        if self.use_memory:
            data = _IN_MEMORY_RUNS.get(run_id)
            if not data or data.get("status") != "queued":
                return None
            data["status"] = "running"
            data["updated_at"] = datetime.now(timezone.utc)
            return RunDocument(**data)

        try:
            async def operation(session: AsyncSession) -> RunDocument | None:
                result = await session.execute(
                    update(RunModel)
                    .where(RunModel.run_id == run_id, RunModel.status == "queued")
                    .values(status="running", updated_at=datetime.now(timezone.utc))
                )
                if result.rowcount != 1:
                    await session.rollback()
                    return None
                await session.commit()
                record = await session.get(RunModel, run_id)
                return self._to_domain(record) if record else None

            claimed = await self._with_session(operation)
            if claimed is not None:
                return claimed
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not claim run for execution.") from exc

    async def append_event(self, event: ExecutionEvent) -> ExecutionEvent:
        if self.use_memory:
            return self._append_event_memory(event)

        try:
            async def operation(session: AsyncSession) -> ExecutionEvent:
                await session.execute(
                    select(RunModel.run_id)
                    .where(RunModel.run_id == event.run_id)
                    .with_for_update()
                )
                maximum = await session.scalar(
                    select(func.max(RunEventModel.sequence)).where(
                        RunEventModel.run_id == event.run_id
                    )
                )
                persisted = event.model_copy(
                    update={"sequence": event.sequence or (maximum or 0) + 1}
                )
                session.add(
                    RunEventModel(
                        event_id=persisted.event_id,
                        run_id=persisted.run_id,
                        sequence=persisted.sequence,
                        schema_version=persisted.schema_version,
                        type=persisted.type,
                        task_id=persisted.task_id,
                        phase=persisted.phase,
                        status=persisted.status,
                        label=persisted.label,
                        payload=persisted.payload,
                        created_at=persisted.created_at,
                    )
                )
                await session.commit()
                return persisted

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not append run event.") from exc

    async def list_events(
        self,
        run_id: str,
        after_event_id: str | None = None,
        limit: int = 200,
    ) -> List[ExecutionEvent]:
        if self.use_memory:
            events = list(_IN_MEMORY_EVENTS.get(run_id, []))
            if after_event_id:
                events = self._after_event_id(events, after_event_id)
            return events[:limit]

        try:
            statement = select(RunEventModel).where(RunEventModel.run_id == run_id)
            if after_event_id:
                after_sequence = await self._with_session(
                    lambda session: session.scalar(
                        select(RunEventModel.sequence).where(
                            RunEventModel.event_id == after_event_id,
                            RunEventModel.run_id == run_id,
                        )
                    )
                )
                if after_sequence is not None:
                    statement = statement.where(RunEventModel.sequence > after_sequence)
            statement = statement.order_by(RunEventModel.sequence.asc()).limit(limit)
            result = await self._with_session(lambda session: session.execute(statement))
            return [
                self._event_to_domain(record)
                for record in result.scalars().all()
            ]
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not list run events.") from exc

    @staticmethod
    def _to_orm_values(document: RunDocument) -> Dict[str, Any]:
        values = document.model_dump()
        values["run_metadata"] = values.pop("metadata")
        values["current_task"] = (
            document.current_task.model_dump() if document.current_task else None
        )
        values["plan"] = [task.model_dump() for task in document.plan]
        values["updated_at"] = document.updated_at
        values["created_at"] = document.created_at
        return values

    @staticmethod
    def _to_domain(record: RunModel) -> RunDocument:
        values = {}
        for column in RunModel.__table__.columns:
            attribute_name = "run_metadata" if column.name == "metadata" else column.name
            values[column.name] = getattr(record, attribute_name)
        return RunDocument(**values)

    @staticmethod
    def _event_to_domain(record: RunEventModel) -> ExecutionEvent:
        return ExecutionEvent(
            event_id=record.event_id,
            run_id=record.run_id,
            sequence=record.sequence,
            schema_version=record.schema_version,
            type=record.type,
            task_id=record.task_id,
            phase=record.phase,
            status=record.status,
            label=record.label,
            payload=record.payload or {},
            created_at=record.created_at,
        )

    @staticmethod
    def _after_event_id(
        events: List[ExecutionEvent],
        after_event_id: str,
    ) -> List[ExecutionEvent]:
        for index, event in enumerate(events):
            if event.event_id == after_event_id:
                return events[index + 1 :]
        return events

    @staticmethod
    def _save_memory(document: RunDocument) -> None:
        _IN_MEMORY_RUNS[document.run_id] = document.model_dump()

    @staticmethod
    def _append_event_memory(event: ExecutionEvent) -> ExecutionEvent:
        events = _IN_MEMORY_EVENTS.setdefault(event.run_id, [])
        persisted = event.model_copy(update={"sequence": event.sequence or len(events) + 1})
        events.append(persisted)
        return persisted

    async def _rollback(self) -> None:
        if self.session is not None:
            await self.session.rollback()
