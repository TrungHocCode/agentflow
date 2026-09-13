"""FastAPI dependency composition for application services."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import get_db
from app.infrastructure.container import (
    build_catalog_service,
    build_run_service,
    build_workflow_service,
)
from app.modules.catalog.service import CatalogService
from app.modules.runs.service import RunService
from app.modules.workflows.service import WorkflowService


async def get_catalog_service(
    db: AsyncSession = Depends(get_db),
) -> CatalogService:
    """Build the Catalog service for one request scope."""

    return build_catalog_service(db)


async def get_workflow_service(
    db: AsyncSession = Depends(get_db),
) -> WorkflowService:
    """Build the Workflow service for one request scope."""

    return build_workflow_service(db)


async def get_persisted_run_service(
    db: AsyncSession = Depends(get_db),
) -> RunService:
    """Build RunService with workflow persistence for run creation."""

    return build_run_service(db)


def get_run_query_service() -> RunService:
    """Build RunService for Mongo-backed run queries and execution operations."""

    return build_run_service()
