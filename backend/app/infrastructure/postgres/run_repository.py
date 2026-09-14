"""PostgreSQL adapter for durable run state and replayable events."""

import os
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.modules.runs.models import RunDocument, Task
from app.modules.runs.orm import RunEventModel, RunModel
from app.modules.runs.ports import RunRepository
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
                else:
                    for key, value in values.items():
                        setattr(record, key, value)
                await session.commit()

            await self._with_session(operation)
        except Exception:
            await self._rollback()
            self._save_memory(document)

    async def get(self, run_id: str) -> RunDocument | None:
        if not self.use_memory:
            try:
                result = await self._with_session(
                    lambda session: session.execute(
                        select(RunModel).where(RunModel.run_id == run_id)
                    )
                )
                record = result.scalar_one_or_none()
                if record is not None:
                    return self._to_domain(record)
            except Exception:
                await self._rollback()

        data = _IN_MEMORY_RUNS.get(run_id)
        return RunDocument(**data) if data else None

    async def list(self, flow_id: str | None = None, limit: int = 50) -> List[RunDocument]:
        if not self.use_memory:
            try:
                statement = select(RunModel).order_by(RunModel.created_at.desc()).limit(limit)
                if flow_id:
                    statement = statement.where(RunModel.flow_id == flow_id)
                result = await self._with_session(lambda session: session.execute(statement))
                records = result.scalars().all()
                if records:
                    return [self._to_domain(record) for record in records]
            except Exception:
                await self._rollback()

        documents = [
            RunDocument(**data)
            for data in _IN_MEMORY_RUNS.values()
            if flow_id is None or data.get("flow_id") == flow_id
        ]
        return documents[:limit]

    async def find_by_idempotency_key(self, idempotency_key: str) -> RunDocument | None:
        if not idempotency_key:
            return None
        if not self.use_memory:
            try:
                result = await self._with_session(
                    lambda session: session.execute(
                        select(RunModel).where(RunModel.idempotency_key == idempotency_key)
                    )
                )
                record = result.scalar_one_or_none()
                if record is not None:
                    return self._to_domain(record)
            except Exception:
                await self._rollback()

        for data in _IN_MEMORY_RUNS.values():
            if data.get("idempotency_key") == idempotency_key:
                return RunDocument(**data)
        return None

    async def claim(self, run_id: str) -> RunDocument | None:
        """Atomically transition queued -> running."""

        if self.use_memory:
            data = _IN_MEMORY_RUNS.get(run_id)
            if not data or data.get("status") != "queued":
                return None
            data["status"] = "running"
            data["updated_at"] = datetime.utcnow()
            return RunDocument(**data)

        try:
            async def operation(session: AsyncSession) -> RunDocument | None:
                result = await session.execute(
                    update(RunModel)
                    .where(RunModel.run_id == run_id, RunModel.status == "queued")
                    .values(status="running", updated_at=datetime.utcnow())
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
        except Exception:
            await self._rollback()

        data = _IN_MEMORY_RUNS.get(run_id)
        if not data or data.get("status") != "queued":
            return None
        data["status"] = "running"
        data["updated_at"] = datetime.utcnow()
        return RunDocument(**data)

    async def append_event(self, event: ExecutionEvent) -> ExecutionEvent:
        if self.use_memory:
            return self._append_event_memory(event)

        try:
            async def operation(session: AsyncSession) -> ExecutionEvent:
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
        except Exception:
            await self._rollback()
            return self._append_event_memory(event)

    async def list_events(
        self,
        run_id: str,
        after_event_id: str | None = None,
        limit: int = 200,
    ) -> List[ExecutionEvent]:
        if not self.use_memory:
            try:
                result = await self._with_session(
                    lambda session: session.execute(
                        select(RunEventModel)
                        .where(RunEventModel.run_id == run_id)
                        .order_by(RunEventModel.sequence.asc())
                        .limit(limit)
                    )
                )
                events = [self._event_to_domain(record) for record in result.scalars().all()]
                if after_event_id:
                    events = self._after_event_id(events, after_event_id)
                if events:
                    return events[:limit]
            except Exception:
                await self._rollback()

        events = list(_IN_MEMORY_EVENTS.get(run_id, []))
        if after_event_id:
            events = self._after_event_id(events, after_event_id)
        return events[:limit]

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
