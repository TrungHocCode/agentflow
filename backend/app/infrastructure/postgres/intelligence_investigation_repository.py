"""Transactional CI investigation round persistence; accepted rounds are immutable."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models import RunModel, WatchlistModel
from app.infrastructure.postgres.models.competitive_intelligence_investigation import RoundModel
from app.modules.competitive_intelligence.investigation_contracts import InvestigationRound
from app.shared.errors import ApplicationError, ConflictError, PersistenceError, ResourceNotFoundError

ALLOWED_TRANSITIONS = {"proposed": ("accepted", "rejected"),
                       "accepted": ("completed", "interrupted", "superseded")}


class PostgresInvestigationRepository:
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
                raise PersistenceError("Could not persist Competitive Intelligence rounds.") from exc

    @staticmethod
    def round_contract(record: RoundModel) -> InvestigationRound:
        return InvestigationRound(id=UUID(record.id), run_id=UUID(record.run_id),
            watchlist_id=UUID(record.watchlist_id), revision_id=UUID(record.revision_id),
            round_number=record.round_number,
            parent_round_id=UUID(record.parent_round_id) if record.parent_round_id else None,
            tasks=record.tasks, scope_digest=record.scope_digest, reserved_calls=record.reserved_calls,
            reserved_tokens=record.reserved_tokens, status=record.status, decided_at=record.decided_at,
            decided_by=record.decided_by, rejection_reasons=list(record.rejection_reasons or []),
            created_at=record.created_at)

    @staticmethod
    async def owned_run(session: AsyncSession, run_id: str, owner_id: str) -> RunModel:
        run = await session.get(RunModel, run_id)
        if run is None or run.user_id != owner_id:
            raise ResourceNotFoundError("Run was not found.", entity="run")
        return run

    async def save_round(self, round: InvestigationRound, owner_id: str) -> InvestigationRound:
        async with self.session() as session:
            watchlist = (await session.execute(select(WatchlistModel).where(
                WatchlistModel.id == str(round.watchlist_id),
                WatchlistModel.owner_id == owner_id))).scalar_one_or_none()
            if watchlist is None:
                raise ResourceNotFoundError("Watchlist was not found.", entity="watchlist")
            await self.owned_run(session, str(round.run_id), owner_id)
            existing = (await session.execute(select(RoundModel).where(
                RoundModel.run_id == str(round.run_id),
                RoundModel.round_number == round.round_number))).scalar_one_or_none()
            if existing is not None:
                raise ConflictError("Round number is already decided for this run.",
                                    code="round_number_decided")
            session.add(RoundModel(id=str(round.id), run_id=str(round.run_id),
                watchlist_id=str(round.watchlist_id), revision_id=str(round.revision_id), owner_id=owner_id,
                round_number=round.round_number,
                parent_round_id=str(round.parent_round_id) if round.parent_round_id else None,
                tasks=[task.model_dump(mode="json") for task in round.tasks], scope_digest=round.scope_digest,
                reserved_calls=round.reserved_calls, reserved_tokens=round.reserved_tokens, status=round.status,
                decided_at=round.decided_at, decided_by=round.decided_by,
                rejection_reasons=list(round.rejection_reasons), created_at=round.created_at))
            await session.flush()
            return round

    async def get_round(self, round_id: str, owner_id: str) -> InvestigationRound:
        async with self.session() as session:
            record = await session.get(RoundModel, round_id)
            if record is None or record.owner_id != owner_id:
                raise ResourceNotFoundError("Investigation round was not found.", entity="round")
            return self.round_contract(record)

    async def list_rounds(self, run_id: str, owner_id: str) -> list[InvestigationRound]:
        async with self.session() as session:
            await self.owned_run(session, run_id, owner_id)
            rows = (await session.execute(select(RoundModel).where(
                RoundModel.run_id == run_id, RoundModel.owner_id == owner_id
                ).order_by(RoundModel.round_number))).scalars().all()
            return [self.round_contract(row) for row in rows]

    async def set_round_status(self, round_id: str, owner_id: str, status: str, decided_by: str,
                               rejection_reasons: list[str] | None = None) -> InvestigationRound:
        async with self.session() as session:
            record = await session.get(RoundModel, round_id)
            if record is None or record.owner_id != owner_id:
                raise ResourceNotFoundError("Investigation round was not found.", entity="round")
            if status not in ALLOWED_TRANSITIONS.get(record.status, ()):
                raise ConflictError(f"Cannot move round from {record.status} to {status}.",
                                    code="round_transition_invalid")
            record.status, record.decided_by = status, decided_by
            if rejection_reasons is not None:
                record.rejection_reasons = list(rejection_reasons)
            await session.flush()
            return self.round_contract(record)
