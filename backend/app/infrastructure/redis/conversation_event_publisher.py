"""Conversation event fan-out adapters."""

import asyncio
import json
from collections import defaultdict, deque
from typing import AsyncIterator, DefaultDict, Deque, Set

from app.db.redis_client import get_redis
from app.modules.conversations.events import ConversationEvent, ConversationEventPublisher


class InMemoryConversationEventPublisher(ConversationEventPublisher):
    def __init__(self, history_size: int = 100) -> None:
        self._history: DefaultDict[str, Deque[ConversationEvent]] = defaultdict(
            lambda: deque(maxlen=history_size)
        )
        self._subscribers: DefaultDict[str, Set[asyncio.Queue[ConversationEvent]]] = defaultdict(set)

    async def publish(self, event: ConversationEvent) -> None:
        self._history[event.conversation_id].append(event)
        for queue in tuple(self._subscribers.get(event.conversation_id, set())):
            await queue.put(event)

    async def _iterate(
        self,
        conversation_id: str,
        turn_id: str | None = None,
    ) -> AsyncIterator[ConversationEvent]:
        queue: asyncio.Queue[ConversationEvent] = asyncio.Queue()
        self._subscribers[conversation_id].add(queue)
        try:
            for event in tuple(self._history.get(conversation_id, ())):
                if turn_id is None or event.turn_id == turn_id:
                    yield event
            while True:
                event = await queue.get()
                if turn_id is None or event.turn_id == turn_id:
                    yield event
        finally:
            self._subscribers[conversation_id].discard(queue)
            if not self._subscribers[conversation_id]:
                self._subscribers.pop(conversation_id, None)

    def subscribe(self, conversation_id: str, turn_id: str | None = None) -> AsyncIterator[ConversationEvent]:
        return self._iterate(conversation_id, turn_id)


class RedisConversationEventPublisher(ConversationEventPublisher):
    def __init__(self, channel_prefix: str = "agentflow:conversation_events:") -> None:
        self.channel_prefix = channel_prefix

    async def publish(self, event: ConversationEvent) -> None:
        client = await get_redis()
        await client.publish(
            f"{self.channel_prefix}{event.conversation_id}",
            event.model_dump_json(),
        )

    async def _iterate(
        self,
        conversation_id: str,
        turn_id: str | None = None,
    ) -> AsyncIterator[ConversationEvent]:
        client = await get_redis()
        pubsub = client.pubsub()
        channel = f"{self.channel_prefix}{conversation_id}"
        await pubsub.subscribe(channel)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("data"):
                    payload = message["data"]
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8")
                    event = ConversationEvent.model_validate(json.loads(payload))
                    if turn_id is None or event.turn_id == turn_id:
                        yield event
                else:
                    await asyncio.sleep(0.05)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    def subscribe(self, conversation_id: str, turn_id: str | None = None) -> AsyncIterator[ConversationEvent]:
        return self._iterate(conversation_id, turn_id)


_IN_MEMORY_CONVERSATION_PUBLISHER = InMemoryConversationEventPublisher()


def get_in_memory_conversation_publisher() -> InMemoryConversationEventPublisher:
    return _IN_MEMORY_CONVERSATION_PUBLISHER
