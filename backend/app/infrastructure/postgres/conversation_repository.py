"""PostgreSQL adapter for the Conversations bounded context."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Tuple, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import AsyncSessionLocal
from app.infrastructure.postgres.models import (
    ConversationMessageModel,
    ConversationModel,
    ConversationTurnEventModel,
    ConversationTurnModel,
)
from app.modules.conversations.events import ConversationEvent
from app.modules.conversations.models import ConversationMessage, ConversationRecord, ConversationTurn
from app.modules.conversations.ports import ConversationRepository
from app.execution.state import Task
from app.shared.errors import ConflictError, PersistenceError


_T = TypeVar("_T")
_IN_MEMORY_CONVERSATIONS: Dict[str, ConversationRecord] = {}
_IN_MEMORY_MESSAGES: Dict[str, List[ConversationMessage]] = {}
_IN_MEMORY_TURNS: Dict[str, ConversationTurn] = {}
_IN_MEMORY_TURN_EVENTS: Dict[str, List[ConversationEvent]] = {}


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
            for turn_id, turn in list(_IN_MEMORY_TURNS.items()):
                if turn.conversation_id == conversation_id:
                    _IN_MEMORY_TURNS.pop(turn_id, None)
                    _IN_MEMORY_TURN_EVENTS.pop(turn_id, None)
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
                        .order_by(
                            ConversationMessageModel.created_at.desc(),
                            ConversationMessageModel.id.desc(),
                        )
                        .limit(limit)
                    )
                )
                records = list(result.scalars().all())
                if records:
                    records.reverse()
                    return [self._message_to_domain(record) for record in records]
            except Exception as exc:
                await self._rollback()
                raise PersistenceError("Could not list conversation messages.") from exc
        return _IN_MEMORY_MESSAGES.get(conversation_id, [])[-limit:]

    async def accept_turn(
        self,
        conversation: ConversationRecord,
        turn: ConversationTurn,
        user_message: ConversationMessage,
        assistant_message: ConversationMessage,
    ) -> ConversationTurn:
        """Atomically enqueue a turn, persist both message identities, and clear stale drafts."""

        if self.use_memory:
            existing = _IN_MEMORY_TURNS.get(turn.id)
            if existing is not None:
                if (
                    existing.conversation_id == turn.conversation_id
                    and existing.user_id == turn.user_id
                    and existing.input_fingerprint == turn.input_fingerprint
                ):
                    return existing
                raise ConflictError("This turn identifier was already used for a different request.")
            if any(
                current.conversation_id == turn.conversation_id
                and current.status in {"queued", "running", "cancel_requested"}
                for current in _IN_MEMORY_TURNS.values()
            ):
                raise ConflictError("This conversation already has an active turn.")
            conversation.draft_plan = []
            conversation.metadata.pop("supervisor_decision", None)
            conversation.metadata.pop("last_turn", None)
            conversation.updated_at = datetime.now(timezone.utc)
            _IN_MEMORY_CONVERSATIONS[conversation.id] = conversation
            _IN_MEMORY_MESSAGES.setdefault(conversation.id, []).extend(
                [user_message, assistant_message]
            )
            turn.last_event_sequence = 1
            _IN_MEMORY_TURNS[turn.id] = turn
            _IN_MEMORY_TURN_EVENTS[turn.id] = [
                ConversationEvent(
                    event_id=str(uuid.uuid4()),
                    conversation_id=turn.conversation_id,
                    turn_id=turn.id,
                    sequence=1,
                    type="turn_accepted",
                    payload={
                        "user_message_id": turn.user_message_id,
                        "assistant_message_id": turn.assistant_message_id,
                        "status": "queued",
                    },
                )
            ]
            return turn

        try:
            async def operation(session: AsyncSession) -> ConversationTurn:
                record = await session.scalar(
                    select(ConversationModel)
                    .where(
                        ConversationModel.id == conversation.id,
                        ConversationModel.user_id == conversation.user_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise ConflictError("The conversation is no longer available.")

                existing = await session.get(ConversationTurnModel, turn.id)
                if existing is not None:
                    if (
                        existing.conversation_id == turn.conversation_id
                        and existing.user_id == turn.user_id
                        and existing.input_fingerprint == turn.input_fingerprint
                    ):
                        return self._turn_to_domain(existing)
                    raise ConflictError("This turn identifier was already used for a different request.")

                active = await session.scalar(
                    select(ConversationTurnModel.id)
                    .where(
                        ConversationTurnModel.conversation_id == turn.conversation_id,
                        ConversationTurnModel.status.in_(
                            ["queued", "running", "cancel_requested"]
                        ),
                    )
                    .limit(1)
                )
                if active is not None:
                    raise ConflictError("This conversation already has an active turn.")

                values = self._orm_values(conversation)
                values["draft_plan"] = []
                values["conversation_metadata"] = {
                    key: value
                    for key, value in conversation.metadata.items()
                    if key not in {"supervisor_decision", "last_turn"}
                }
                for key, value in values.items():
                    setattr(record, key, value)
                session.add(self._message_to_orm(user_message))
                session.add(self._message_to_orm(assistant_message))
                await session.flush()
                turn.last_event_sequence = 1
                session.add(self._turn_to_orm(turn))
                session.add(
                    ConversationTurnEventModel(
                        event_id=str(uuid.uuid4()),
                        turn_id=turn.id,
                        conversation_id=turn.conversation_id,
                        sequence=1,
                        event_type="turn_accepted",
                        payload={
                            "user_message_id": turn.user_message_id,
                            "assistant_message_id": turn.assistant_message_id,
                            "status": "queued",
                        },
                    )
                )
                await session.commit()
                return turn

            return await self._with_session(operation)
        except ConflictError:
            await self._rollback()
            raise
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not durably enqueue conversation turn.") from exc

    async def get_turn(
        self,
        conversation_id: str,
        turn_id: str,
        user_id: str,
    ) -> ConversationTurn | None:
        if self.use_memory:
            turn = _IN_MEMORY_TURNS.get(turn_id)
            return turn if turn and turn.conversation_id == conversation_id and turn.user_id == user_id else None
        try:
            result = await self._with_session(
                lambda session: session.execute(
                    select(ConversationTurnModel)
                    .join(ConversationModel, ConversationModel.id == ConversationTurnModel.conversation_id)
                    .where(
                        ConversationTurnModel.id == turn_id,
                        ConversationTurnModel.conversation_id == conversation_id,
                        ConversationTurnModel.user_id == user_id,
                        ConversationModel.user_id == user_id,
                    )
                )
            )
            record = result.scalar_one_or_none()
            return self._turn_to_domain(record) if record else None
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not load conversation turn.") from exc

    async def list_turns(
        self,
        conversation_id: str,
        user_id: str,
        limit: int = 50,
    ) -> List[ConversationTurn]:
        if self.use_memory:
            return sorted(
                [turn for turn in _IN_MEMORY_TURNS.values()
                 if turn.conversation_id == conversation_id and turn.user_id == user_id],
                key=lambda turn: turn.created_at,
                reverse=True,
            )[:limit]
        try:
            result = await self._with_session(
                lambda session: session.execute(
                    select(ConversationTurnModel)
                    .join(ConversationModel, ConversationModel.id == ConversationTurnModel.conversation_id)
                    .where(
                        ConversationTurnModel.conversation_id == conversation_id,
                        ConversationTurnModel.user_id == user_id,
                        ConversationModel.user_id == user_id,
                    )
                    .order_by(ConversationTurnModel.created_at.desc())
                    .limit(limit)
                )
            )
            return [self._turn_to_domain(record) for record in result.scalars().all()]
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not list conversation turns.") from exc

    async def claim_next_turn(self, worker_id: str) -> ConversationTurn | None:
        if self.use_memory:
            queued = sorted(
                (turn for turn in _IN_MEMORY_TURNS.values() if turn.status == "queued"),
                key=lambda turn: turn.created_at,
            )
            if not queued:
                return None
            turn = queued[0]
            now = datetime.now(timezone.utc)
            turn.status = "running"
            turn.worker_id = worker_id
            turn.started_at = now
            turn.heartbeat_at = now
            self._memory_append_event(turn, "planning_started", {"status": "running"})
            return turn
        try:
            async def operation(session: AsyncSession) -> ConversationTurn | None:
                record = await session.scalar(
                    select(ConversationTurnModel)
                    .where(ConversationTurnModel.status == "queued")
                    .order_by(ConversationTurnModel.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if record is None:
                    return None
                now = datetime.now(timezone.utc)
                record.status = "running"
                record.worker_id = worker_id
                record.started_at = now
                record.heartbeat_at = now
                record.last_event_sequence += 1
                session.add(
                    ConversationTurnEventModel(
                        event_id=str(uuid.uuid4()),
                        turn_id=record.id,
                        conversation_id=record.conversation_id,
                        sequence=record.last_event_sequence,
                        event_type="planning_started",
                        payload={"status": "running"},
                    )
                )
                await session.commit()
                return self._turn_to_domain(record)

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not claim queued conversation turn.") from exc

    async def heartbeat_turn(self, turn_id: str, worker_id: str) -> bool:
        now = datetime.now(timezone.utc)
        if self.use_memory:
            turn = _IN_MEMORY_TURNS.get(turn_id)
            if turn is None or turn.worker_id != worker_id or turn.status != "running":
                return False
            turn.heartbeat_at = now
            return True
        try:
            async def operation(session: AsyncSession) -> bool:
                record = await session.get(ConversationTurnModel, turn_id)
                if record is None or record.worker_id != worker_id or record.status != "running":
                    return False
                record.heartbeat_at = now
                await session.commit()
                return True

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not update conversation turn heartbeat.") from exc

    async def save_turn(self, turn: ConversationTurn) -> ConversationTurn:
        if self.use_memory:
            _IN_MEMORY_TURNS[turn.id] = turn
            return turn
        try:
            async def operation(session: AsyncSession) -> None:
                record = await session.get(ConversationTurnModel, turn.id)
                if record is None:
                    raise ConflictError("Conversation turn no longer exists.")
                for key, value in self._turn_orm_values(turn).items():
                    setattr(record, key, value)
                await session.commit()

            await self._with_session(operation)
            return turn
        except ConflictError:
            await self._rollback()
            raise
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not save conversation turn outcome.") from exc

    async def finalize_turn(
        self,
        turn: ConversationTurn,
        conversation: ConversationRecord,
        assistant_message: ConversationMessage,
        events: List[Tuple[str, Dict[str, object]]],
    ) -> List[ConversationEvent]:
        """Commit the final message, draft snapshot, turn state, and terminal events together."""

        if self.use_memory:
            _IN_MEMORY_TURNS[turn.id] = turn
            _IN_MEMORY_CONVERSATIONS[conversation.id] = conversation
            messages = _IN_MEMORY_MESSAGES.setdefault(conversation.id, [])
            for index, current in enumerate(messages):
                if current.id == assistant_message.id:
                    messages[index] = assistant_message
                    break
            else:
                messages.append(assistant_message)
            return [self._memory_append_event(turn, event_type, payload) for event_type, payload in events]
        try:
            async def operation(session: AsyncSession) -> List[ConversationEvent]:
                turn_record = await session.scalar(
                    select(ConversationTurnModel)
                    .where(ConversationTurnModel.id == turn.id)
                    .with_for_update()
                )
                conversation_record = await session.get(ConversationModel, conversation.id)
                message_record = await session.get(ConversationMessageModel, assistant_message.id)
                if turn_record is None or conversation_record is None or message_record is None:
                    raise ConflictError("Conversation turn state is incomplete and cannot be finalized.")
                for key, value in self._turn_orm_values(turn).items():
                    setattr(turn_record, key, value)
                for key, value in self._orm_values(conversation).items():
                    setattr(conversation_record, key, value)
                message_record.content = assistant_message.content
                message_record.message_metadata = assistant_message.metadata
                written_events: List[ConversationEvent] = []
                for event_type, payload in events:
                    turn_record.last_event_sequence += 1
                    event_id = str(uuid.uuid4())
                    created_at = datetime.now(timezone.utc)
                    session.add(
                        ConversationTurnEventModel(
                            event_id=event_id,
                            turn_id=turn.id,
                            conversation_id=turn.conversation_id,
                            sequence=turn_record.last_event_sequence,
                            event_type=event_type,
                            payload=payload,
                            created_at=created_at,
                        )
                    )
                    written_events.append(
                        ConversationEvent(
                            event_id=event_id,
                            conversation_id=turn.conversation_id,
                            turn_id=turn.id,
                            sequence=turn_record.last_event_sequence,
                            type=event_type,
                            payload=payload,
                            created_at=created_at,
                        )
                    )
                turn.last_event_sequence = turn_record.last_event_sequence
                await session.commit()
                return written_events

            return await self._with_session(operation)
        except ConflictError:
            await self._rollback()
            raise
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not atomically finalize conversation turn.") from exc

    async def save_message(self, message: ConversationMessage) -> ConversationMessage:
        if self.use_memory:
            messages = _IN_MEMORY_MESSAGES.setdefault(message.conversation_id, [])
            for index, current in enumerate(messages):
                if current.id == message.id:
                    messages[index] = message
                    break
            else:
                messages.append(message)
            return message
        try:
            async def operation(session: AsyncSession) -> None:
                record = await session.get(ConversationMessageModel, message.id)
                if record is None:
                    session.add(self._message_to_orm(message))
                else:
                    record.content = message.content
                    record.message_metadata = message.metadata
                await session.commit()

            await self._with_session(operation)
            return message
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not update conversation message.") from exc

    async def request_turn_cancel(
        self,
        conversation_id: str,
        turn_id: str,
        user_id: str,
    ) -> ConversationTurn | None:
        if self.use_memory:
            turn = await self.get_turn(conversation_id, turn_id, user_id)
            if turn is not None and turn.status in {"queued", "running"}:
                turn.status = "cancel_requested"
                self._memory_append_event(turn, "turn_cancel_requested", {"status": "cancel_requested"})
            return turn
        try:
            async def operation(session: AsyncSession) -> ConversationTurn | None:
                record = await session.scalar(
                    select(ConversationTurnModel)
                    .join(ConversationModel, ConversationModel.id == ConversationTurnModel.conversation_id)
                    .where(
                        ConversationTurnModel.id == turn_id,
                        ConversationTurnModel.conversation_id == conversation_id,
                        ConversationTurnModel.user_id == user_id,
                        ConversationModel.user_id == user_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    return None
                if record.status in {"queued", "running"}:
                    record.status = "cancel_requested"
                    record.last_event_sequence += 1
                    session.add(
                        ConversationTurnEventModel(
                            event_id=str(uuid.uuid4()),
                            turn_id=record.id,
                            conversation_id=record.conversation_id,
                            sequence=record.last_event_sequence,
                            event_type="turn_cancel_requested",
                            payload={"status": record.status},
                        )
                    )
                    await session.commit()
                return self._turn_to_domain(record)

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not request conversation turn cancellation.") from exc

    async def append_turn_event(
        self,
        turn_id: str,
        event_type: str,
        payload: dict,
    ) -> ConversationEvent:
        if self.use_memory:
            turn = _IN_MEMORY_TURNS[turn_id]
            return self._memory_append_event(turn, event_type, payload)
        try:
            async def operation(session: AsyncSession) -> ConversationEvent:
                record = await session.scalar(
                    select(ConversationTurnModel)
                    .where(ConversationTurnModel.id == turn_id)
                    .with_for_update()
                )
                if record is None:
                    raise ConflictError("Conversation turn no longer exists.")
                record.last_event_sequence += 1
                event_id = str(uuid.uuid4())
                created_at = datetime.now(timezone.utc)
                session.add(
                    ConversationTurnEventModel(
                        event_id=event_id,
                        turn_id=record.id,
                        conversation_id=record.conversation_id,
                        sequence=record.last_event_sequence,
                        event_type=event_type,
                        payload=payload,
                        created_at=created_at,
                    )
                )
                await session.commit()
                return ConversationEvent(
                    event_id=event_id,
                    conversation_id=record.conversation_id,
                    turn_id=record.id,
                    sequence=record.last_event_sequence,
                    type=event_type,
                    payload=payload,
                    created_at=created_at,
                )

            return await self._with_session(operation)
        except ConflictError:
            await self._rollback()
            raise
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not persist conversation progress event.") from exc

    async def list_turn_events(
        self,
        conversation_id: str,
        turn_id: str,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> List[ConversationEvent]:
        if self.use_memory:
            return [
                event for event in _IN_MEMORY_TURN_EVENTS.get(turn_id, [])
                if event.conversation_id == conversation_id and event.sequence > after_sequence
            ][:limit]
        try:
            result = await self._with_session(
                lambda session: session.execute(
                    select(ConversationTurnEventModel)
                    .where(
                        ConversationTurnEventModel.conversation_id == conversation_id,
                        ConversationTurnEventModel.turn_id == turn_id,
                        ConversationTurnEventModel.sequence > after_sequence,
                    )
                    .order_by(ConversationTurnEventModel.sequence.asc())
                    .limit(limit)
                )
            )
            return [
                ConversationEvent(
                    event_id=record.event_id,
                    conversation_id=record.conversation_id,
                    turn_id=record.turn_id,
                    sequence=record.sequence,
                    type=record.event_type,
                    payload=record.payload or {},
                    created_at=record.created_at,
                )
                for record in result.scalars().all()
            ]
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not replay conversation progress events.") from exc

    async def recover_stale_turns(
        self,
        stale_before: datetime,
        queue_expired_before: datetime,
    ) -> List[ConversationTurn]:
        """Make abandoned worker leases and overlong queue waits visible as terminal outcomes."""

        if self.use_memory:
            now = datetime.now(timezone.utc)
            recovered: List[ConversationTurn] = []
            for turn in _IN_MEMORY_TURNS.values():
                if turn.status == "queued" and turn.created_at < queue_expired_before:
                    turn.status = "failed"
                    turn.error_code = "conversation_queue_timeout"
                    turn.error_category = "capacity"
                    turn.error_message = "The request waited too long for an available worker."
                elif turn.status in {"running", "cancel_requested"} and turn.heartbeat_at and turn.heartbeat_at < stale_before:
                    turn.status = "cancelled" if turn.status == "cancel_requested" else "interrupted"
                    turn.error_code = None if turn.status == "cancelled" else "worker_interrupted"
                    turn.error_category = None if turn.status == "cancelled" else "worker"
                    turn.error_message = None if turn.status == "cancelled" else "The worker stopped before this request completed."
                else:
                    continue
                turn.completed_at = now
                assistant = next(
                    (message for message in _IN_MEMORY_MESSAGES.get(turn.conversation_id, [])
                     if message.id == turn.assistant_message_id),
                    None,
                )
                if assistant:
                    assistant.content = turn.error_message or "Yêu cầu đã được hủy."
                    assistant.metadata = {
                        **assistant.metadata,
                        "turn_id": turn.id,
                        "status": turn.status,
                        "error_id": turn.error_id,
                    }
                event_type = "turn_cancelled" if turn.status == "cancelled" else "turn_interrupted" if turn.status == "interrupted" else "turn_failed"
                self._memory_append_event(
                    turn,
                    event_type,
                    {
                        "status": turn.status,
                        "code": turn.error_code,
                        "message": turn.error_message,
                        "error_id": turn.error_id,
                    },
                )
                recovered.append(turn)
            return recovered
        try:
            async def operation(session: AsyncSession) -> List[ConversationTurn]:
                records = (await session.scalars(
                    select(ConversationTurnModel)
                    .where(
                        ((ConversationTurnModel.status == "queued") & (ConversationTurnModel.created_at < queue_expired_before))
                        | ((ConversationTurnModel.status.in_(["running", "cancel_requested"])) & (ConversationTurnModel.heartbeat_at < stale_before))
                    )
                    .with_for_update(skip_locked=True)
                    .limit(100)
                )).all()
                recovered: List[ConversationTurn] = []
                for record in records:
                    if record.status == "queued":
                        record.status = "failed"
                        record.error_code = "conversation_queue_timeout"
                        record.error_category = "capacity"
                        record.error_message = "The request waited too long for an available worker."
                        event_type = "turn_failed"
                    elif record.status == "cancel_requested":
                        record.status = "cancelled"
                        record.error_code = None
                        record.error_category = None
                        record.error_message = None
                        event_type = "turn_cancelled"
                    else:
                        record.status = "interrupted"
                        record.error_code = "worker_interrupted"
                        record.error_category = "worker"
                        record.error_message = "The worker stopped before this request completed."
                        event_type = "turn_interrupted"
                    record.completed_at = datetime.now(timezone.utc)
                    record.last_event_sequence += 1
                    session.add(
                        ConversationTurnEventModel(
                            event_id=str(uuid.uuid4()),
                            turn_id=record.id,
                            conversation_id=record.conversation_id,
                            sequence=record.last_event_sequence,
                            event_type=event_type,
                            payload={"status": record.status, "code": record.error_code},
                        )
                    )
                    assistant = await session.get(ConversationMessageModel, record.assistant_message_id)
                    if assistant:
                        assistant.content = record.error_message or "Yêu cầu đã được hủy."
                        assistant.message_metadata = {
                            **(assistant.message_metadata or {}),
                            "turn_id": record.id,
                            "status": record.status,
                            "error_id": record.error_id,
                        }
                    recovered.append(self._turn_to_domain(record))
                await session.commit()
                return recovered

            return await self._with_session(operation)
        except Exception as exc:
            await self._rollback()
            raise PersistenceError("Could not recover abandoned conversation turns.") from exc

    @staticmethod
    def _message_to_orm(message: ConversationMessage) -> ConversationMessageModel:
        return ConversationMessageModel(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role,
            content=message.content,
            message_metadata=message.metadata,
            created_at=message.created_at,
            updated_at=message.created_at,
        )

    @staticmethod
    def _turn_to_orm(turn: ConversationTurn) -> ConversationTurnModel:
        return ConversationTurnModel(**PostgresConversationRepository._turn_orm_values(turn))

    @staticmethod
    def _turn_orm_values(turn: ConversationTurn) -> Dict[str, Any]:
        return {
            "id": turn.id,
            "conversation_id": turn.conversation_id,
            "user_id": turn.user_id,
            "user_message_id": turn.user_message_id,
            "assistant_message_id": turn.assistant_message_id,
            "input_fingerprint": turn.input_fingerprint,
            "status": turn.status,
            "outcome": turn.outcome,
            "assistant_content": turn.assistant_content,
            "plan": [task.model_dump(mode="json") for task in turn.plan],
            "error_code": turn.error_code,
            "error_category": turn.error_category,
            "error_id": turn.error_id,
            "error_message": turn.error_message,
            "retryable": turn.retryable,
            "worker_id": turn.worker_id,
            "last_event_sequence": turn.last_event_sequence,
            "created_at": turn.created_at,
            "started_at": turn.started_at,
            "heartbeat_at": turn.heartbeat_at,
            "completed_at": turn.completed_at,
        }

    @staticmethod
    def _turn_to_domain(record: ConversationTurnModel) -> ConversationTurn:
        return ConversationTurn(
            **{
                **{key: value for key, value in record.__dict__.items() if not key.startswith("_")},
                "plan": [Task.model_validate(task) for task in (record.plan or [])],
            }
        )

    @staticmethod
    def _memory_append_event(
        turn: ConversationTurn,
        event_type: str,
        payload: dict,
    ) -> ConversationEvent:
        turn.last_event_sequence += 1
        event = ConversationEvent(
            conversation_id=turn.conversation_id,
            turn_id=turn.id,
            sequence=turn.last_event_sequence,
            type=event_type,
            payload=payload,
        )
        _IN_MEMORY_TURN_EVENTS.setdefault(turn.id, []).append(event)
        return event

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
