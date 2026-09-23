"""Persistence port owned by the Conversations bounded context."""

from typing import List, Protocol

from app.modules.conversations.models import ConversationMessage, ConversationRecord


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
