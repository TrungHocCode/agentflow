"""Composition helpers for wiring concrete adapters into application services."""

import os

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.adapters import LangGraphExecutionAdapter
from app.execution.ports import ExecutionPort
from app.infrastructure.health_probe import DatabaseHealthProbe
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository
from app.infrastructure.postgres.conversation_repository import (
    PostgresConversationRepository,
)
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository
from app.infrastructure.redis.event_publisher import (
    RedisRunEventPublisher,
    get_in_memory_publisher,
)
from app.infrastructure.redis.run_queue import (
    RedisRunCommandQueue,
    get_in_memory_queue,
)
from app.modules.catalog.service import CatalogService
from app.modules.conversations.service import ConversationService
from app.modules.runs.events import RunEventPublisher
from app.modules.runs.queue import RunCommandQueue
from app.modules.runs.service import RunService
from app.modules.system.health import HealthService
from app.modules.workflows.service import WorkflowService


def build_execution_port() -> ExecutionPort:
    """Return the current in-process execution adapter."""

    return LangGraphExecutionAdapter()


def build_health_service() -> HealthService:
    """Compose the operational health service."""

    return HealthService(probe=DatabaseHealthProbe())


def build_run_queue() -> RunCommandQueue:
    """Select the Redis queue or deterministic local queue."""

    if os.getenv("TESTING", "").lower() == "true":
        return get_in_memory_queue()
    return RedisRunCommandQueue()


def build_event_publisher() -> RunEventPublisher:
    """Select live fan-out implementation for the current deployment."""

    if os.getenv("TESTING", "").lower() == "true":
        return get_in_memory_publisher()
    return RedisRunEventPublisher()


def build_catalog_service(session: AsyncSession) -> CatalogService:
    """Compose the Catalog application service."""

    return CatalogService(repository=PostgresCatalogRepository(session))


def build_workflow_service(session: AsyncSession) -> WorkflowService:
    """Compose the Workflow application service."""

    return WorkflowService(repository=PostgresWorkflowRepository(session))


def build_conversation_service(session: AsyncSession | None = None) -> ConversationService:
    """Compose the Build Phase conversation service."""

    return ConversationService(
        repository=PostgresConversationRepository(session),
        execution_port=build_execution_port(),
    )


def build_run_service(session: AsyncSession | None = None) -> RunService:
    """Compose RunService without leaking concrete adapters into the module."""

    return RunService(
        run_repository=PostgresRunRepository(session),
        workflow_repository=PostgresWorkflowRepository(session) if session else None,
        execution_port=build_execution_port(),
        command_queue=build_run_queue(),
        event_publisher=build_event_publisher(),
    )
