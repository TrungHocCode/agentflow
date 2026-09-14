"""Run command queue adapters."""

import asyncio
import json
from typing import Optional

from app.db.redis_client import get_redis
from app.modules.runs.queue import RunCommandQueue
from app.shared.commands import RunCommand


class InMemoryRunCommandQueue(RunCommandQueue):
    """Deterministic queue used by tests and local no-infrastructure mode."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[RunCommand] = asyncio.Queue()

    async def enqueue(self, command: RunCommand) -> None:
        await self._queue.put(command)

    async def dequeue(self, timeout: int = 1) -> Optional[RunCommand]:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=max(timeout, 0.01))
        except asyncio.TimeoutError:
            return None


class RedisRunCommandQueue(RunCommandQueue):
    """At-most-once Redis List queue: BRPOP removes a command on dequeue."""

    def __init__(self, key: str = "agentflow:run_commands") -> None:
        self.key = key

    async def enqueue(self, command: RunCommand) -> None:
        client = await get_redis()
        await client.lpush(self.key, command.model_dump_json())

    async def dequeue(self, timeout: int = 1) -> Optional[RunCommand]:
        client = await get_redis()
        item = await client.brpop(self.key, timeout=max(timeout, 0))
        if item is None:
            return None
        _, payload = item
        return RunCommand.model_validate(json.loads(payload))


_IN_MEMORY_QUEUE = InMemoryRunCommandQueue()


def get_in_memory_queue() -> InMemoryRunCommandQueue:
    """Return the process-wide test/local queue."""

    return _IN_MEMORY_QUEUE
