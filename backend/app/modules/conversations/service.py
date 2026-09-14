"""Application service for the Build Phase conversation lifecycle."""

import uuid
from datetime import datetime
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage

from app.execution.ports import ExecutionPort
from app.execution.state import State, Task
from app.modules.conversations.models import (
    ConversationMessage,
    ConversationRecord,
)
from app.modules.conversations.ports import ConversationRepository


class ConversationService:
    """Coordinates durable conversation history and plan drafting."""

    def __init__(
        self,
        repository: ConversationRepository,
        execution_port: ExecutionPort,
    ) -> None:
        self.repository = repository
        self.execution_port = execution_port

    async def create_conversation(
        self,
        workflow_id: str | None = None,
        title: str | None = None,
        metadata: Dict[str, Any] | None = None,
        user_id: str = "default_user",
    ) -> ConversationRecord:
        now = datetime.utcnow()
        conversation = ConversationRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workflow_id=workflow_id,
            title=title,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        return await self.repository.create(conversation)

    async def get_conversation(
        self,
        conversation_id: str,
        user_id: str = "default_user",
    ) -> ConversationRecord | None:
        return await self.repository.get(conversation_id, user_id)

    async def list_conversations(
        self,
        user_id: str = "default_user",
        limit: int = 50,
    ) -> List[ConversationRecord]:
        return await self.repository.list(user_id=user_id, limit=limit)

    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 200,
    ) -> List[ConversationMessage]:
        return await self.repository.list_messages(conversation_id, limit=limit)

    async def send_message(
        self,
        conversation_id: str,
        content: str,
        user_id: str = "default_user",
    ) -> ConversationRecord | None:
        conversation = await self.get_conversation(conversation_id, user_id)
        if conversation is None or conversation.status == "archived":
            return None

        user_message = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="user",
            content=content,
            created_at=datetime.utcnow(),
        )
        await self.repository.add_message(user_message)

        if conversation.draft_plan:
            result_state = await self.execution_port.continue_conversation(
                conversation.id,
                content,
            )
        else:
            initial_state: State = {
                "messages": [HumanMessage(content=content)],
                "plan": [],
                "current_task": None,
                "logs": [],
                "result_storage": [],
                "mode": "conversation",
                "metadata": conversation.metadata,
            }
            result_state = await self.execution_port.create_plan(
                conversation.id,
                initial_state,
            )

        conversation.draft_plan = self._normalize_tasks(result_state.get("plan") or [])
        conversation.status = "waiting_for_user"
        conversation.updated_at = datetime.utcnow()
        await self.repository.save(conversation)

        for message in self._assistant_messages(result_state.get("messages") or []):
            await self.repository.add_message(
                ConversationMessage(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation.id,
                    role="assistant",
                    content=message,
                    created_at=datetime.utcnow(),
                )
            )
        return conversation

    @staticmethod
    def _assistant_messages(messages: List[Any]) -> List[str]:
        result: List[str] = []
        for message in messages:
            if isinstance(message, AIMessage):
                result.append(str(message.content))
            elif isinstance(message, dict) and message.get("role") == "assistant":
                result.append(str(message.get("content", "")))
        return [message for message in result if message]

    @staticmethod
    def _normalize_tasks(values: List[Task | Dict[str, Any]]) -> List[Task]:
        return [
            value if isinstance(value, Task) else Task.model_validate(value)
            for value in values
        ]
