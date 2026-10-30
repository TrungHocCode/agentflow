"""Background scheduler for CI watchlist runs; fires due approved schedules exactly once."""

import asyncio
import logging
from datetime import datetime, timezone

from app.infrastructure.container import build_run_service, build_scheduling_service
from app.core.config import settings, validate_runtime_settings
from app.shared.observability import configure_logging

logger = logging.getLogger(__name__)
TICK_SECONDS = 60


async def main() -> None:
    configure_logging(settings.LOG_LEVEL)
    validate_runtime_settings()
    logger.info("AgentFlow CI scheduler starting")
    service = build_scheduling_service()
    run_service = build_run_service()
    while True:
        try:
            now = datetime.now(timezone.utc)
            reports = await service.tick(now)
            for report in reports:
                logger.info("Schedule tick decision", extra={"report": report})
            await run_service.mark_stale_runs_zombie(now)
        except asyncio.CancelledError:
            logger.info("AgentFlow CI scheduler stopped")
            raise
        except Exception:
            logger.exception("Unhandled CI scheduler error")
        await asyncio.sleep(TICK_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
