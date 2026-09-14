"""Conversation progress event port."""

import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Protocol

from pydantic import BaseModel, Field


class ConversationEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    turn_id: str
    type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationEventPublisher(Protocol):
    async def publish(self, event: ConversationEvent) -> None:
        ...

    def subscribe(self, conversation_id: str, turn_id: str | None = None) -> AsyncIterator[ConversationEvent]:
        ...
