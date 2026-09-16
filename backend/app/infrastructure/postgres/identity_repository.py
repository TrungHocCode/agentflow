"""PostgreSQL adapter for identity data with an isolated test store."""

import os
from datetime import datetime, timezone
from typing import Dict, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.models.identity import UserModel
from app.modules.identity.models import UserRecord
from app.modules.identity.ports import UserRepository
from app.shared.errors import PersistenceError


_IN_MEMORY_USERS: Dict[str, Tuple[UserRecord, str]] = {}


class PostgresUserRepository(UserRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    @property
    def use_memory(self) -> bool:
        return os.getenv("TESTING", "").lower() == "true"

    async def create(self, user: UserRecord, password_hash: str) -> UserRecord:
        if self.use_memory:
            _IN_MEMORY_USERS[user.id] = (user, password_hash)
            return user
        try:
            self.session.add(
                UserModel(
                    id=user.id,
                    email=user.email,
                    username=user.username,
                    password_hash=password_hash,
                    display_name=user.display_name,
                    is_active=user.is_active,
                    last_login_at=user.last_login_at,
                )
            )
            await self.session.commit()
            return user
        except Exception as exc:
            await self.session.rollback()
            raise PersistenceError("Could not create user.") from exc

    async def get_by_email(self, email: str) -> tuple[UserRecord, str] | None:
        if self.use_memory:
            return next((entry for entry in _IN_MEMORY_USERS.values() if entry[0].email == email), None)
        try:
            result = await self.session.execute(select(UserModel).where(UserModel.email == email))
            row = result.scalar_one_or_none()
            return self._to_entry(row) if row else None
        except Exception as exc:
            raise PersistenceError("Could not load user by email.") from exc

    async def get(self, user_id: str) -> UserRecord | None:
        if self.use_memory:
            entry = _IN_MEMORY_USERS.get(user_id)
            return entry[0] if entry else None
        try:
            row = await self.session.get(UserModel, user_id)
            return self._to_user(row) if row else None
        except Exception as exc:
            raise PersistenceError("Could not load user.") from exc

    async def touch_last_login(self, user_id: str) -> UserRecord | None:
        now = datetime.now(timezone.utc)
        if self.use_memory:
            entry = _IN_MEMORY_USERS.get(user_id)
            if not entry:
                return None
            updated = entry[0].model_copy(update={"last_login_at": now, "updated_at": now})
            _IN_MEMORY_USERS[user_id] = (updated, entry[1])
            return updated
        try:
            row = await self.session.get(UserModel, user_id)
            if row is None:
                return None
            row.last_login_at = now
            await self.session.commit()
            # Server-side timestamp columns can be expired by commit. Refresh
            # explicitly inside the async session before mapping the ORM row.
            await self.session.refresh(row)
            return self._to_user(row)
        except Exception as exc:
            await self.session.rollback()
            raise PersistenceError("Could not update user login timestamp.") from exc

    @staticmethod
    def _to_user(row: UserModel) -> UserRecord:
        created_at = row.created_at or datetime.now(timezone.utc)
        updated_at = row.updated_at or created_at
        return UserRecord(
            id=row.id,
            email=row.email,
            username=row.username,
            display_name=row.display_name,
            is_active=row.is_active,
            last_login_at=row.last_login_at,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _to_entry(self, row: UserModel) -> tuple[UserRecord, str]:
        return self._to_user(row), row.password_hash
