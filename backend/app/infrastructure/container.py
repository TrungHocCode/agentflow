"""Composition helpers for wiring concrete adapters into application services."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.adapters import LangGraphExecutionAdapter
from app.execution.ports import ExecutionPort
from app.infrastructure.mongodb.run_repository import MongoRunRepository
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository
from app.modules.catalog.service import CatalogService
from app.modules.runs.service import RunService
from app.modules.workflows.service import WorkflowService


def build_execution_port() -> ExecutionPort:
    """Return the current in-process execution adapter."""

    return LangGraphExecutionAdapter()


def build_catalog_service(session: AsyncSession) -> CatalogService:
    """Compose the Catalog application service."""

    return CatalogService(repository=PostgresCatalogRepository(session))


def build_workflow_service(session: AsyncSession) -> WorkflowService:
    """Compose the Workflow application service."""

    return WorkflowService(repository=PostgresWorkflowRepository(session))


def build_run_service(session: AsyncSession | None = None) -> RunService:
    """Compose RunService without leaking concrete adapters into the module."""

    return RunService(
        run_repository=MongoRunRepository(),
        workflow_repository=PostgresWorkflowRepository(session) if session else None,
        execution_port=build_execution_port(),
    )
