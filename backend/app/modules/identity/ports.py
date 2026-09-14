"""Persistence port for the identity bounded context."""

from typing import Protocol

from app.modules.identity.models import UserRecord


class UserRepository(Protocol):
    async def create(self, user: UserRecord, password_hash: str) -> UserRecord:
        ...

    async def get_by_email(self, email: str) -> tuple[UserRecord, str] | None:
        ...

    async def get(self, user_id: str) -> UserRecord | None:
        ...

    async def touch_last_login(self, user_id: str) -> UserRecord | None:
        ...
