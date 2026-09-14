"""Queue port for asynchronous run commands."""

from typing import Protocol

from app.shared.commands import RunCommand


class RunCommandQueue(Protocol):
    async def enqueue(self, command: RunCommand) -> None:
        ...

    async def dequeue(self, timeout: int = 1) -> RunCommand | None:
        ...


class DiscardingRunCommandQueue:
    """Compatibility queue for direct unit construction without composition."""

    async def enqueue(self, command: RunCommand) -> None:
        return None

    async def dequeue(self, timeout: int = 1) -> RunCommand | None:
        return None
