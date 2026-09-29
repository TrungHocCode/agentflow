"""Persistence port owned by the Conversations bounded context."""

from datetime import datetime
from typing import Dict, List, Protocol, Tuple

from app.modules.conversations.events import ConversationEvent
from app.modules.conversations.models import ConversationMessage, ConversationRecord, ConversationTurn


class ConversationRepository(Protocol):
    async def create(self, conversation: ConversationRecord) -> ConversationRecord:
        ...

    async def get(self, conversation_id: str, user_id: str) -> ConversationRecord | None:
        ...

    async def list(self, user_id: str, limit: int = 50) -> List[ConversationRecord]:
        ...

    async def delete(self, conversation_id: str, user_id: str) -> bool:
        ...

    async def save(self, conversation: ConversationRecord) -> ConversationRecord:
        ...

    async def add_message(self, message: ConversationMessage) -> ConversationMessage:
        ...

    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 200,
    ) -> List[ConversationMessage]:
        ...

    async def accept_turn(
        self,
        conversation: ConversationRecord,
        turn: ConversationTurn,
        user_message: ConversationMessage,
        assistant_message: ConversationMessage,
    ) -> ConversationTurn:
        ...

    async def get_turn(
        self,
        conversation_id: str,
        turn_id: str,
        user_id: str,
    ) -> ConversationTurn | None:
        ...

    async def list_turns(
        self,
        conversation_id: str,
        user_id: str,
        limit: int = 50,
    ) -> List[ConversationTurn]:
        ...

    async def claim_next_turn(self, worker_id: str) -> ConversationTurn | None:
        ...

    async def heartbeat_turn(self, turn_id: str, worker_id: str) -> bool:
        ...

    async def save_turn(self, turn: ConversationTurn) -> ConversationTurn:
        ...

    async def finalize_turn(
        self,
        turn: ConversationTurn,
        conversation: ConversationRecord,
        assistant_message: ConversationMessage,
        events: List[Tuple[str, Dict[str, object]]],
    ) -> List[ConversationEvent]:
        ...

    async def save_message(self, message: ConversationMessage) -> ConversationMessage:
        ...

    async def request_turn_cancel(
        self,
        conversation_id: str,
        turn_id: str,
        user_id: str,
    ) -> ConversationTurn | None:
        ...

    async def append_turn_event(
        self,
        turn_id: str,
        event_type: str,
        payload: dict,
    ) -> ConversationEvent:
        ...

    async def list_turn_events(
        self,
        conversation_id: str,
        turn_id: str,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> List[ConversationEvent]:
        ...

    async def recover_stale_turns(
        self,
        stale_before: datetime,
        queue_expired_before: datetime,
    ) -> List[ConversationTurn]:
        ...
