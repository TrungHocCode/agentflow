"""Transactional CI brief persistence; recorded briefs are immutable."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models import WatchlistModel
from app.infrastructure.postgres.models.competitive_intelligence_brief import BriefModel
from app.modules.competitive_intelligence.brief_contracts import (
    BriefFinding, BriefSourceCoverage, IntelligenceBrief,
)
from app.shared.errors import ApplicationError, PersistenceError, ResourceNotFoundError


class PostgresBriefRepository:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.session_factory = session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except ApplicationError:
                await session.rollback()
                raise
            except Exception as exc:
                await session.rollback()
                raise PersistenceError("Could not persist Competitive Intelligence briefs.") from exc

    @staticmethod
    def brief_contract(record: BriefModel) -> IntelligenceBrief:
        return IntelligenceBrief(id=UUID(record.id), watchlist_id=UUID(record.watchlist_id),
            revision_id=UUID(record.revision_id), run_id=UUID(record.run_id), schema_version="1",
            outcome=record.outcome, quality=record.quality, summary=record.summary,
            findings=[BriefFinding(**finding) for finding in record.findings or []],
            source_coverage=[BriefSourceCoverage(**entry) for entry in record.source_coverage or []],
            conflicts=list(record.conflicts or []), limitations=list(record.limitations or []),
            advisory_actions=list(record.advisory_actions or []), artifact_id=record.artifact_id,
            artifact_uri=record.artifact_uri, artifact_hash=record.artifact_hash,
            observed_from=record.observed_from, observed_to=record.observed_to, created_at=record.created_at)

    async def save_brief(self, brief: IntelligenceBrief, owner_id: str) -> IntelligenceBrief:
        async with self.session() as session:
            watchlist = (await session.execute(select(WatchlistModel).where(
                WatchlistModel.id == str(brief.watchlist_id),
                WatchlistModel.owner_id == owner_id))).scalar_one_or_none()
            if watchlist is None:
                raise ResourceNotFoundError("Watchlist was not found.", entity="watchlist")
            session.add(BriefModel(id=str(brief.id), watchlist_id=str(brief.watchlist_id),
                revision_id=str(brief.revision_id), run_id=str(brief.run_id), owner_id=owner_id,
                schema_version=brief.schema_version, outcome=brief.outcome, quality=brief.quality,
                summary=brief.summary, findings=[finding.model_dump(mode="json") for finding in brief.findings],
                source_coverage=[entry.model_dump(mode="json") for entry in brief.source_coverage],
                conflicts=list(brief.conflicts), limitations=list(brief.limitations),
                advisory_actions=list(brief.advisory_actions), artifact_id=brief.artifact_id,
                artifact_uri=brief.artifact_uri, artifact_hash=brief.artifact_hash,
                observed_from=brief.observed_from, observed_to=brief.observed_to, created_at=brief.created_at))
            await session.flush()
            return brief

    async def get_brief(self, brief_id: str, owner_id: str) -> IntelligenceBrief:
        async with self.session() as session:
            record = await session.get(BriefModel, brief_id)
            if record is None or record.owner_id != owner_id:
                raise ResourceNotFoundError("Brief was not found.", entity="brief")
            return self.brief_contract(record)

    async def list_briefs(self, watchlist_id: str, owner_id: str, limit: int,
                          offset: int) -> list[IntelligenceBrief]:
        async with self.session() as session:
            rows = (await session.execute(select(BriefModel).where(
                BriefModel.watchlist_id == watchlist_id, BriefModel.owner_id == owner_id
                ).order_by(desc(BriefModel.created_at)).limit(min(max(limit, 1), 100)).offset(max(offset, 0)))
                ).scalars().all()
            return [self.brief_contract(row) for row in rows]

    async def count_briefs(self, watchlist_id: str, owner_id: str) -> int:
        async with self.session() as session:
            return await session.scalar(select(func.count()).select_from(BriefModel).where(
                BriefModel.watchlist_id == watchlist_id, BriefModel.owner_id == owner_id)) or 0
