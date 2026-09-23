"""PostgreSQL adapter for the Conversations bounded context."""

import os
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models import ConversationMessageModel, ConversationModel
from app.modules.conversations.models import ConversationMessage, ConversationRecord
from app.modules.conversations.ports import ConversationRepository
from app.execution.state import Task
from app.shared.errors import PersistenceError


_T = TypeVar("_T")
_IN_MEMORY_CONVERSATIONS: Dict[str, ConversationRecord] = {}
_IN_MEMORY_MESSAGES: Dict[str, List[ConversationMessage]] = {}


class PostgresConversationRepository(ConversationRepository):
    """Persist conversation metadata and messages with local fallback."""

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

    async def create(self, conversation: ConversationRecord) -> ConversationRecord:
        if self.use_memory:
            _IN_MEMORY_CONVERSATIONS[conversation.id] = conversation
            return conversation
        try:
            async def operation(session: AsyncSession) -> None:
                session.add(self._to_orm(conversation))
                await session.commit()

            await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not create conversation.") from exc
        return conversation

    async def get(self, conversation_id: str, user_id: str) -> ConversationRecord | None:
        if not self.use_memory:
            try:
                result = await self._with_session(
                    lambda session: session.execute(
                        select(ConversationModel).where(
                            ConversationModel.id == conversation_id,
                            ConversationModel.user_id == user_id,
                        )
                    )
                )
                record = result.scalar_one_or_none()
                if record:
                    return self._to_domain(record)
            except Exception as exc:
                await self._rollback()
                raise PersistenceError("Could not load conversation.") from exc

        conversation = _IN_MEMORY_CONVERSATIONS.get(conversation_id)
        if conversation and conversation.user_id == user_id:
            return conversation
        return None

    async def list(self, user_id: str, limit: int = 50) -> List[ConversationRecord]:
        if not self.use_memory:
            try:
                result = await self._with_session(
                    lambda session: session.execute(
                        select(ConversationModel)
                        .where(ConversationModel.user_id == user_id)
                        .order_by(ConversationModel.updated_at.desc())
                        .limit(limit)
                    )
                )
                records = result.scalars().all()
                if records:
                    return [self._to_domain(record) for record in records]
            except Exception as exc:
                await self._rollback()
                raise PersistenceError("Could not list conversations.") from exc

        records = [
            conversation
            for conversation in _IN_MEMORY_CONVERSATIONS.values()
            if conversation.user_id == user_id
        ]
        return records[:limit]

    async def delete(self, conversation_id: str, user_id: str) -> bool:
        if self.use_memory:
            conversation = _IN_MEMORY_CONVERSATIONS.get(conversation_id)
            if conversation is None or conversation.user_id != user_id:
                return False
            _IN_MEMORY_CONVERSATIONS.pop(conversation_id, None)
            _IN_MEMORY_MESSAGES.pop(conversation_id, None)
            return True

        try:
            async def operation(session: AsyncSession) -> bool:
                result = await session.execute(
                    delete(ConversationModel).where(
                        ConversationModel.id == conversation_id,
                        ConversationModel.user_id == user_id,
                    )
                )
                await session.commit()
                return bool(result.rowcount)

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not delete conversation.") from exc

    async def save(self, conversation: ConversationRecord) -> ConversationRecord:
        if self.use_memory:
            _IN_MEMORY_CONVERSATIONS[conversation.id] = conversation
            return conversation
        try:
            async def operation(session: AsyncSession) -> None:
                record = await session.get(ConversationModel, conversation.id)
                values = self._orm_values(conversation)
                if record is None:
                    session.add(ConversationModel(**values))
                else:
                    for key, value in values.items():
                        setattr(record, key, value)
                await session.commit()

            await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not save conversation.") from exc
        return conversation

    async def add_message(self, message: ConversationMessage) -> ConversationMessage:
        if self.use_memory:
            _IN_MEMORY_MESSAGES.setdefault(message.conversation_id, []).append(message)
            return message
        try:
            async def operation(session: AsyncSession) -> None:
                session.add(
                    ConversationMessageModel(
                        id=message.id,
                        conversation_id=message.conversation_id,
                        role=message.role,
                        content=message.content,
                        message_metadata=message.metadata,
                        created_at=message.created_at,
                        updated_at=message.created_at,
                    )
                )
                await session.commit()

            await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not save conversation message.") from exc
        return message

    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 200,
    ) -> List[ConversationMessage]:
        if not self.use_memory:
            try:
                result = await self._with_session(
                    lambda session: session.execute(
                        select(ConversationMessageModel)
                        .where(ConversationMessageModel.conversation_id == conversation_id)
                        .order_by(ConversationMessageModel.created_at.asc())
                        .limit(limit)
                    )
                )
                records = result.scalars().all()
                if records:
                    return [self._message_to_domain(record) for record in records]
            except Exception as exc:
                await self._rollback()
                raise PersistenceError("Could not list conversation messages.") from exc
        return _IN_MEMORY_MESSAGES.get(conversation_id, [])[:limit]

    @staticmethod
    def _to_orm(conversation: ConversationRecord) -> ConversationModel:
        return ConversationModel(**PostgresConversationRepository._orm_values(conversation))

    @staticmethod
    def _orm_values(conversation: ConversationRecord) -> Dict[str, Any]:
        return {
            "id": conversation.id,
            "user_id": conversation.user_id,
            "workflow_id": conversation.workflow_id,
            "title": conversation.title,
            "status": conversation.status,
            "draft_plan": [task.model_dump() for task in conversation.draft_plan],
            "conversation_metadata": conversation.metadata,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        }

    @staticmethod
    def _to_domain(record: ConversationModel) -> ConversationRecord:
        return ConversationRecord(
            id=record.id,
            user_id=record.user_id,
            workflow_id=record.workflow_id,
            title=record.title,
            status=record.status,
            draft_plan=[Task.model_validate(task) for task in (record.draft_plan or [])],
            metadata=record.conversation_metadata or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _message_to_domain(record: ConversationMessageModel) -> ConversationMessage:
        return ConversationMessage(
            id=record.id,
            conversation_id=record.conversation_id,
            role=record.role,
            content=record.content,
            metadata=record.message_metadata or {},
            created_at=record.created_at,
        )

    async def _rollback(self) -> None:
        if self.session is not None:
            await self.session.rollback()
