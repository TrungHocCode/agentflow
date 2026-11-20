"""Owner-scoped storage of the account's own products; one is the default."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Callable
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models import MyProductModel
from app.modules.competitive_intelligence.models import MyProduct
from app.shared.errors import ApplicationError, ConflictError, PersistenceError, ResourceNotFoundError

_FIELDS = ("name", "description", "description_en", "website", "market")


class PostgresMyProductRepository:
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
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("You already have a product with this name.") from exc
            except Exception as exc:
                await session.rollback()
                raise PersistenceError("Could not persist your products.") from exc

    @staticmethod
    def contract(record: MyProductModel) -> MyProduct:
        return MyProduct(id=UUID(record.id), name=record.name, description=record.description,
            description_en=record.description_en, website=record.website, market=record.market,
            known_competitors=list(record.known_competitors), is_default=record.is_default,
            created_at=record.created_at, updated_at=record.updated_at)

    @staticmethod
    async def _owned(session: AsyncSession, owner_id: str, product_id: str) -> MyProductModel:
        record = (await session.execute(select(MyProductModel).where(
            MyProductModel.id == product_id, MyProductModel.owner_id == owner_id))).scalar_one_or_none()
        if record is None:
            raise ResourceNotFoundError("Product was not found.", entity="my_product")
        return record

    async def list(self, owner_id: str) -> list[MyProduct]:
        async with self.session() as session:
            rows = await session.execute(select(MyProductModel).where(MyProductModel.owner_id == owner_id)
                .order_by(MyProductModel.created_at, MyProductModel.id))
            return [self.contract(row) for row in rows.scalars()]

    async def get(self, owner_id: str, product_id: str) -> MyProduct:
        async with self.session() as session:
            return self.contract(await self._owned(session, owner_id, product_id))

    async def find_by_name(self, owner_id: str, name: str) -> MyProduct | None:
        async with self.session() as session:
            record = (await session.execute(select(MyProductModel).where(
                MyProductModel.owner_id == owner_id,
                func.lower(MyProductModel.name) == name.strip().lower()))).scalar_one_or_none()
            return self.contract(record) if record else None

    async def create(self, owner_id: str, data: MyProduct) -> MyProduct:
        now = datetime.now(timezone.utc)
        async with self.session() as session:
            has_any = (await session.execute(select(MyProductModel.id).where(
                MyProductModel.owner_id == owner_id).limit(1))).first() is not None
            record = MyProductModel(id=str(uuid4()), owner_id=owner_id, known_competitors=data.known_competitors,
                is_default=not has_any, created_at=now, updated_at=now,
                **{field: getattr(data, field) for field in _FIELDS})
            session.add(record)
            await session.flush()
            return self.contract(record)

    async def update(self, owner_id: str, product_id: str, data: MyProduct) -> MyProduct:
        async with self.session() as session:
            record = await self._owned(session, owner_id, product_id)
            for field in _FIELDS:
                setattr(record, field, getattr(data, field))
            record.known_competitors = data.known_competitors
            record.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return self.contract(record)

    async def set_default(self, owner_id: str, product_id: str) -> MyProduct:
        async with self.session() as session:
            record = await self._owned(session, owner_id, product_id)
            if not record.is_default:
                # Clear first so the partial unique index never sees two defaults.
                await session.execute(update(MyProductModel).where(MyProductModel.owner_id == owner_id,
                    MyProductModel.is_default.is_(True)).values(is_default=False))
                record.is_default = True
                await session.flush()
            return self.contract(record)

    async def delete(self, owner_id: str, product_id: str) -> None:
        async with self.session() as session:
            record = await self._owned(session, owner_id, product_id)
            was_default = record.is_default
            await session.delete(record)
            await session.flush()
            if was_default:
                oldest = (await session.execute(select(MyProductModel).where(MyProductModel.owner_id == owner_id)
                    .order_by(MyProductModel.created_at, MyProductModel.id).limit(1))).scalar_one_or_none()
                if oldest is not None:
                    oldest.is_default = True
