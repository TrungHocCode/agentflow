"""FastAPI dependency composition for application services."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import get_db
from app.infrastructure.container import (
    build_catalog_service,
    build_conversation_service,
    build_health_service,
    build_run_service,
    build_workflow_service,
)
from app.modules.catalog.service import CatalogService
from app.modules.conversations.service import ConversationService
from app.modules.runs.service import RunService
from app.modules.system.health import HealthService
from app.modules.workflows.service import WorkflowService
from app.shared.ids import DEFAULT_USER_ID


def get_current_user_id() -> str:
    """Return the current user boundary until authentication is introduced."""

    return DEFAULT_USER_ID


def get_health_service() -> HealthService:
    """Build the operational health service."""

    return build_health_service()


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


async def get_conversation_service(
    db: AsyncSession = Depends(get_db),
) -> ConversationService:
    """Build the Conversation service for one request scope."""

    return build_conversation_service(db)


async def get_persisted_run_service(
    db: AsyncSession = Depends(get_db),
) -> RunService:
    """Build RunService with workflow persistence for run creation."""

    return build_run_service(db)


def get_run_query_service() -> RunService:
    """Build RunService for PostgreSQL-backed run queries and execution operations."""

    return build_run_service()
