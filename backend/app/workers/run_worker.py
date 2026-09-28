"""Background worker for asynchronous workflow runs."""

import asyncio
import logging

from app.infrastructure.container import build_run_queue, build_run_service
from app.modules.runs.queue import RunCommandQueue
from app.modules.runs.service import RunService
from app.core.config import settings, validate_runtime_settings
from app.shared.observability import bind_context, configure_logging


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
        with bind_context(
            request_id=command.metadata.get("request_id"),
            conversation_id=command.metadata.get("conversation_id"),
            run_id=command.run_id,
            command_id=command.command_id,
        ):
            logger.info("Executing queued workflow run")
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
    configure_logging(settings.LOG_LEVEL)
    validate_runtime_settings()
    logger.info("AgentFlow execution worker starting")
    worker = RunWorker(
        service=build_run_service(),
        command_queue=build_run_queue(),
    )
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
