"""Background worker for asynchronous workflow runs."""

import asyncio
import logging

from app.infrastructure.container import build_run_queue, build_run_service
from app.modules.runs.queue import RunCommandQueue
from app.modules.runs.service import RunService


logger = logging.getLogger(__name__)


class RunWorker:
    """Consumes one run command at a time under the initial at-most-once policy."""

    def __init__(
        self,
        service: RunService,
        command_queue: RunCommandQueue,
    ) -> None:
        self.service = service
        self.command_queue = command_queue

    async def process_next(self, timeout: int = 1) -> bool:
        """Process one command and return whether work was found."""

        command = await self.command_queue.dequeue(timeout=timeout)
        if command is None:
            return False
        logger.info("Executing run %s from command %s", command.run_id, command.command_id)
        await self.service.execute_queued_run(command.run_id)
        return True

    async def run_forever(self) -> None:
        """Run until the process receives cancellation."""

        logger.info("AgentFlow execution worker started")
        while True:
            try:
                await self.process_next(timeout=1)
            except asyncio.CancelledError:
                logger.info("AgentFlow execution worker stopped")
                raise
            except Exception:
                logger.exception("Unhandled execution worker error")
                await asyncio.sleep(1)


async def main() -> None:
    worker = RunWorker(
        service=build_run_service(),
        command_queue=build_run_queue(),
    )
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
