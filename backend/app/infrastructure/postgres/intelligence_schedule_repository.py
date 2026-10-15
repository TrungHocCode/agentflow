"""Transactional CI schedule claims and owner-scoped notifications."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Callable
from uuid import UUID, uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models import WatchlistModel
from app.infrastructure.postgres.models.competitive_intelligence_scheduling import (
    NotificationModel, ScheduleModel,
)
from app.modules.competitive_intelligence.schedule_contracts import Notification, Schedule
from app.modules.competitive_intelligence.schedule_timing import next_occurrence, resolve_timezone
from app.shared.errors import ApplicationError, PersistenceError, ResourceNotFoundError


class PostgresScheduleRepository:
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
                raise PersistenceError("Could not persist Competitive Intelligence schedules.") from exc

    @staticmethod
    def schedule_contract(record: ScheduleModel) -> Schedule:
        return Schedule(id=UUID(record.id), watchlist_id=UUID(record.watchlist_id), owner_id=record.owner_id,
            cadence=record.cadence, timezone=record.timezone, run_at=record.run_at,
            day_of_week=record.day_of_week, active=record.active, next_due_at=record.next_due_at,
            created_at=record.created_at, updated_at=record.updated_at)

    @staticmethod
    def notification_contract(record: NotificationModel) -> Notification:
        return Notification(id=UUID(record.id), owner_id=record.owner_id,
            watchlist_id=UUID(record.watchlist_id) if record.watchlist_id else None,
            run_id=UUID(record.run_id) if record.run_id else None, kind=record.kind, title=record.title,
            body=record.body or "", read_at=record.read_at, created_at=record.created_at)

    async def create_schedule(self, watchlist_id: str, owner_id: str, cadence: str, timezone_name: str,
                              run_at: str, day_of_week: int | None, now: datetime) -> Schedule:
        resolve_timezone(timezone_name)
        async with self.session() as session:
            watchlist = (await session.execute(select(WatchlistModel).where(
                WatchlistModel.id == watchlist_id, WatchlistModel.owner_id == owner_id,
                WatchlistModel.status == "active"))).scalar_one_or_none()
            if watchlist is None:
                raise ResourceNotFoundError("Active watchlist was not found.", entity="watchlist")
            record = ScheduleModel(id=str(uuid4()), watchlist_id=watchlist_id, owner_id=owner_id,
                cadence=cadence, timezone=timezone_name, run_at=run_at, day_of_week=day_of_week,
                active=True, next_due_at=next_occurrence(now, cadence, timezone_name, run_at, day_of_week))
            session.add(record)
            await session.flush()
            return self.schedule_contract(record)

    async def list_schedules(self, watchlist_id: str, owner_id: str) -> list[Schedule]:
        async with self.session() as session:
            rows = (await session.execute(select(ScheduleModel).where(
                ScheduleModel.watchlist_id == watchlist_id, ScheduleModel.owner_id == owner_id
                ).order_by(desc(ScheduleModel.created_at)))).scalars().all()
            return [self.schedule_contract(row) for row in rows]

    async def update_schedule(self, schedule_id: str, watchlist_id: str, owner_id: str, now: datetime, *,
                              cadence: str | None = None, timezone_name: str | None = None,
                              run_at: str | None = None, day_of_week: int | None = None,
                              active: bool | None = None) -> Schedule:
        async with self.session() as session:
            record = await session.get(ScheduleModel, schedule_id)
            if record is None or record.owner_id != owner_id or record.watchlist_id != watchlist_id:
                raise ResourceNotFoundError("Schedule was not found.", entity="schedule")
            if cadence is not None:
                record.cadence = cadence
            if timezone_name is not None:
                resolve_timezone(timezone_name)
                record.timezone = timezone_name
            if run_at is not None:
                record.run_at = run_at
            if day_of_week is not None:
                record.day_of_week = day_of_week
            if active is not None:
                record.active = active
            record.next_due_at = next_occurrence(now, record.cadence, record.timezone, record.run_at,
                                                 record.day_of_week)
            await session.flush()
            return self.schedule_contract(record)

    async def claim_due(self, now: datetime, limit: int = 10) -> list[Schedule]:
        """Lock due active schedules (single winner via SKIP LOCKED) without advancing them."""
        async with self.session() as session:
            rows = (await session.execute(select(ScheduleModel).where(
                ScheduleModel.active.is_(True), ScheduleModel.next_due_at <= now
                ).order_by(ScheduleModel.next_due_at).limit(limit).with_for_update(skip_locked=True))
                ).scalars().all()
            return [self.schedule_contract(row) for row in rows]

    async def advance(self, schedule_id: str, owner_id: str, now: datetime) -> Schedule:
        async with self.session() as session:
            record = await session.get(ScheduleModel, schedule_id, with_for_update=True)
            if record is None or record.owner_id != owner_id:
                raise ResourceNotFoundError("Schedule was not found.", entity="schedule")
            record.next_due_at = next_occurrence(now, record.cadence, record.timezone, record.run_at,
                                                 record.day_of_week)
            await session.flush()
            return self.schedule_contract(record)

    async def notify(self, owner_id: str, kind: str, title: str, body: str = "",
                     watchlist_id: str | None = None, run_id: str | None = None) -> Notification:
        async with self.session() as session:
            record = NotificationModel(id=str(uuid4()), owner_id=owner_id, watchlist_id=watchlist_id,
                run_id=run_id, kind=kind, title=title, body=body, created_at=datetime.now(timezone.utc))
            session.add(record)
            await session.flush()
            return self.notification_contract(record)

    async def list_notifications(self, owner_id: str, limit: int, unread_only: bool) -> list[Notification]:
        async with self.session() as session:
            query = select(NotificationModel).where(NotificationModel.owner_id == owner_id)
            if unread_only:
                query = query.where(NotificationModel.read_at.is_(None))
            rows = (await session.execute(query.order_by(desc(NotificationModel.created_at))
                                          .limit(min(max(limit, 1), 100)))).scalars().all()
            return [self.notification_contract(row) for row in rows]

    async def count_unread(self, owner_id: str) -> int:
        async with self.session() as session:
            return await session.scalar(select(func.count()).select_from(NotificationModel).where(
                NotificationModel.owner_id == owner_id, NotificationModel.read_at.is_(None))) or 0

    async def count_notifications(self, owner_id: str) -> int:
        async with self.session() as session:
            return await session.scalar(select(func.count()).select_from(NotificationModel).where(
                NotificationModel.owner_id == owner_id)) or 0

    async def mark_read(self, notification_id: str, owner_id: str, now: datetime) -> Notification:
        async with self.session() as session:
            record = await session.get(NotificationModel, notification_id)
            if record is None or record.owner_id != owner_id:
                raise ResourceNotFoundError("Notification was not found.", entity="notification")
            record.read_at = now
            await session.flush()
            return self.notification_contract(record)
