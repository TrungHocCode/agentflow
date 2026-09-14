"""PostgreSQL adapter for structured results, evidence and artifact metadata."""

import os
from typing import Any, Awaitable, Callable, Dict, List, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models.results import ArtifactModel, EvidenceModel, ResultModel
from app.modules.results.models import ArtifactRecord, EvidenceRecord, ResultRecord
from app.modules.results.ports import ResearchDataRepository
from app.shared.errors import PersistenceError


_T = TypeVar("_T")
_RESULTS: Dict[str, ResultRecord] = {}
_EVIDENCE: Dict[str, EvidenceRecord] = {}
_ARTIFACTS: Dict[str, ArtifactRecord] = {}


class PostgresResearchRepository(ResearchDataRepository):
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

    async def _with_session(self, operation: Callable[[AsyncSession], Awaitable[_T]]) -> _T:
        if self.session is not None:
            return await operation(self.session)
        async with self.session_factory() as session:
            return await operation(session)

    async def save_result(self, result: ResultRecord) -> ResultRecord:
        if self.use_memory:
            _RESULTS[result.id] = result
            return result
        try:
            async def operation(session: AsyncSession) -> ResultRecord:
                record = await session.get(ResultModel, result.id)
                values = self._result_values(result)
                if record is None:
                    session.add(ResultModel(**values))
                else:
                    for key, value in values.items():
                        setattr(record, key, value)
                await session.commit()
                return result

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not save research result.") from exc

    async def list_results(self, run_id: str, limit: int = 200) -> List[ResultRecord]:
        if self.use_memory:
            return [item for item in _RESULTS.values() if item.run_id == run_id][:limit]
        try:
            result = await self._with_session(
                lambda session: session.execute(
                    select(ResultModel)
                    .where(ResultModel.run_id == run_id)
                    .order_by(ResultModel.created_at.asc())
                    .limit(limit)
                )
            )
            return [self._result_domain(row) for row in result.scalars().all()]
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not list research results.") from exc

    async def save_evidence(self, evidence: EvidenceRecord) -> EvidenceRecord:
        if self.use_memory:
            _EVIDENCE[evidence.id] = evidence
            return evidence
        try:
            async def operation(session: AsyncSession) -> EvidenceRecord:
                record = await session.get(EvidenceModel, evidence.id)
                values = self._evidence_values(evidence)
                if record is None:
                    session.add(EvidenceModel(**values))
                else:
                    for key, value in values.items():
                        setattr(record, key, value)
                await session.commit()
                return evidence

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not save research evidence.") from exc

    async def list_evidence(self, run_id: str, limit: int = 200) -> List[EvidenceRecord]:
        if self.use_memory:
            return [item for item in _EVIDENCE.values() if item.run_id == run_id][:limit]
        try:
            result = await self._with_session(
                lambda session: session.execute(
                    select(EvidenceModel)
                    .where(EvidenceModel.run_id == run_id)
                    .order_by(EvidenceModel.collected_at.asc())
                    .limit(limit)
                )
            )
            return [self._evidence_domain(row) for row in result.scalars().all()]
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not list research evidence.") from exc

    async def get_evidence(self, evidence_id: str, run_id: str) -> EvidenceRecord | None:
        if self.use_memory:
            item = _EVIDENCE.get(evidence_id)
            return item if item and item.run_id == run_id else None
        try:
            result = await self._with_session(
                lambda session: session.execute(
                    select(EvidenceModel).where(
                        EvidenceModel.id == evidence_id,
                        EvidenceModel.run_id == run_id,
                    )
                )
            )
            row = result.scalar_one_or_none()
            return self._evidence_domain(row) if row else None
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not load research evidence.") from exc

    async def save_artifact(self, artifact: ArtifactRecord) -> ArtifactRecord:
        if self.use_memory:
            _ARTIFACTS[artifact.id] = artifact
            return artifact
        try:
            async def operation(session: AsyncSession) -> ArtifactRecord:
                record = await session.get(ArtifactModel, artifact.id)
                values = self._artifact_values(artifact)
                if record is None:
                    session.add(ArtifactModel(**values))
                else:
                    for key, value in values.items():
                        setattr(record, key, value)
                await session.commit()
                return artifact

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not save artifact metadata.") from exc

    async def list_artifacts(self, run_id: str, limit: int = 200) -> List[ArtifactRecord]:
        if self.use_memory:
            return [item for item in _ARTIFACTS.values() if item.run_id == run_id][:limit]
        try:
            result = await self._with_session(
                lambda session: session.execute(
                    select(ArtifactModel)
                    .where(ArtifactModel.run_id == run_id)
                    .order_by(ArtifactModel.created_at.asc())
                    .limit(limit)
                )
            )
            return [self._artifact_domain(row) for row in result.scalars().all()]
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not list artifact metadata.") from exc

    async def get_artifact(self, artifact_id: str, run_id: str) -> ArtifactRecord | None:
        if self.use_memory:
            item = _ARTIFACTS.get(artifact_id)
            return item if item and item.run_id == run_id else None
        try:
            result = await self._with_session(
                lambda session: session.execute(
                    select(ArtifactModel).where(
                        ArtifactModel.id == artifact_id,
                        ArtifactModel.run_id == run_id,
                    )
                )
            )
            row = result.scalar_one_or_none()
            return self._artifact_domain(row) if row else None
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not load artifact metadata.") from exc

    @staticmethod
    def _result_values(item: ResultRecord) -> Dict[str, Any]:
        return {
            "id": item.id,
            "run_id": item.run_id,
            "task_execution_id": item.task_execution_id,
            "task_id": item.task_id,
            "result_type": item.result_type,
            "content": item.content,
            "schema_version": item.schema_version,
            "result_metadata": item.metadata,
            "created_at": item.created_at,
        }

    @staticmethod
    def _result_domain(row: ResultModel) -> ResultRecord:
        return ResultRecord(
            id=row.id,
            run_id=row.run_id,
            task_execution_id=row.task_execution_id,
            task_id=row.task_id,
            result_type=row.result_type,
            content=row.content,
            schema_version=row.schema_version,
            metadata=row.result_metadata or {},
            created_at=row.created_at,
        )

    @staticmethod
    def _evidence_values(item: EvidenceRecord) -> Dict[str, Any]:
        return {
            "id": item.id,
            "run_id": item.run_id,
            "task_execution_id": item.task_execution_id,
            "source_url": item.source_url,
            "source_title": item.source_title,
            "source_type": item.source_type,
            "excerpt": item.excerpt,
            "content_hash": item.content_hash,
            "evidence_metadata": item.metadata,
            "collected_at": item.collected_at,
        }

    @staticmethod
    def _evidence_domain(row: EvidenceModel) -> EvidenceRecord:
        return EvidenceRecord(
            id=row.id,
            run_id=row.run_id,
            task_execution_id=row.task_execution_id,
            source_url=row.source_url,
            source_title=row.source_title,
            source_type=row.source_type,
            excerpt=row.excerpt,
            content_hash=row.content_hash,
            metadata=row.evidence_metadata or {},
            collected_at=row.collected_at,
        )

    @staticmethod
    def _artifact_values(item: ArtifactRecord) -> Dict[str, Any]:
        return {
            "id": item.id,
            "user_id": item.user_id,
            "run_id": item.run_id,
            "task_execution_id": item.task_execution_id,
            "name": item.name,
            "content_type": item.content_type,
            "storage_uri": item.storage_uri,
            "size_bytes": item.size_bytes,
            "checksum": item.checksum,
            "artifact_metadata": item.metadata,
            "created_at": item.created_at,
        }

    @staticmethod
    def _artifact_domain(row: ArtifactModel) -> ArtifactRecord:
        return ArtifactRecord(
            id=row.id,
            user_id=row.user_id,
            run_id=row.run_id,
            task_execution_id=row.task_execution_id,
            name=row.name,
            content_type=row.content_type,
            storage_uri=row.storage_uri,
            size_bytes=row.size_bytes,
            checksum=row.checksum,
            metadata=row.artifact_metadata or {},
            created_at=row.created_at,
        )

    async def _rollback(self) -> None:
        if self.session is not None:
            await self.session.rollback()
