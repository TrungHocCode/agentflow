"""Realtime event publisher adapters."""

import asyncio
import json
from collections import defaultdict
from typing import AsyncIterator, DefaultDict, Set

from app.db.redis_client import get_redis
from app.modules.runs.events import RunEventPublisher
from app.shared.events import ExecutionEvent


class InMemoryRunEventPublisher(RunEventPublisher):
    """Process-local fan-out publisher for tests and local development."""

    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, Set[asyncio.Queue[ExecutionEvent]]] = defaultdict(set)

    async def publish(self, event: ExecutionEvent) -> None:
        for queue in tuple(self._subscribers.get(event.run_id, set())):
            await queue.put(event)

    async def _iterate(self, run_id: str) -> AsyncIterator[ExecutionEvent]:
        queue: asyncio.Queue[ExecutionEvent] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                self._subscribers.pop(run_id, None)

    def subscribe(self, run_id: str) -> AsyncIterator[ExecutionEvent]:
        return self._iterate(run_id)


class RedisRunEventPublisher(RunEventPublisher):
    """Redis Pub/Sub adapter for live progress fan-out."""

    def __init__(self, channel_prefix: str = "agentflow:run_events:") -> None:
        self.channel_prefix = channel_prefix

    async def publish(self, event: ExecutionEvent) -> None:
        client = await get_redis()
        await client.publish(
            f"{self.channel_prefix}{event.run_id}",
            event.model_dump_json(),
        )

    async def _iterate(self, run_id: str) -> AsyncIterator[ExecutionEvent]:
        client = await get_redis()
        pubsub = client.pubsub()
        await pubsub.subscribe(f"{self.channel_prefix}{run_id}")
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message and message.get("data"):
                    payload = message["data"]
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8")
                    yield ExecutionEvent.model_validate(json.loads(payload))
                else:
                    await asyncio.sleep(0.05)
        finally:
            await pubsub.unsubscribe(f"{self.channel_prefix}{run_id}")
            await pubsub.aclose()

    def subscribe(self, run_id: str) -> AsyncIterator[ExecutionEvent]:
        return self._iterate(run_id)


_IN_MEMORY_PUBLISHER = InMemoryRunEventPublisher()


def get_in_memory_publisher() -> InMemoryRunEventPublisher:
    """Return the process-wide test/local publisher."""

    return _IN_MEMORY_PUBLISHER
