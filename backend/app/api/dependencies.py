"""FastAPI dependency composition for application services."""

import os

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_client import get_db
from app.infrastructure.container import (
    build_catalog_service,
    build_conversation_service,
    build_health_service,
    build_auth_service,
    build_run_service,
    build_workflow_service,
    build_research_result_service,
)
from app.modules.catalog.service import CatalogService
from app.modules.conversations.service import ConversationService
from app.modules.runs.service import RunService
from app.modules.system.health import HealthService
from app.modules.workflows.service import WorkflowService
from app.shared.ids import DEFAULT_USER_ID
from app.modules.identity.service import IdentityService
from app.modules.results.service import ResearchResultService
from app.infrastructure.artifacts.storage import LocalArtifactStorage
from app.infrastructure.redis.rate_limiter import NoopRateLimiter, RedisRateLimiter
from app.modules.system.ports import RateLimiter
from app.shared.errors import AuthenticationError


async def get_current_user_id(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Resolve the authenticated user, retaining a test-only compatibility user."""

    if os.getenv("TESTING", "").lower() == "true":
        return DEFAULT_USER_ID
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        # Recheck account activity on each authenticated request so disabling an
        # account takes effect before the access token naturally expires.
        user = await build_auth_service(db).current_user(authorization.split(" ", 1)[1].strip())
        return user.id
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired access token.") from exc


def get_health_service() -> HealthService:
    """Build the operational health service."""

    return build_health_service()


async def get_catalog_service(
    db: AsyncSession = Depends(get_db),
) -> CatalogService:
    """Build the Catalog service for one request scope."""

    return build_catalog_service(db)


async def get_auth_service(
    db: AsyncSession = Depends(get_db),
) -> IdentityService:
    """Build the identity service for one request scope."""

    return build_auth_service(db)


async def get_workflow_service(
    db: AsyncSession = Depends(get_db),
) -> WorkflowService:
    """Build the Workflow service for one request scope."""

    return build_workflow_service(db)


def get_conversation_service() -> ConversationService:
    """Build a session-independent service; each repository operation owns its DB session."""

    return build_conversation_service()


async def get_persisted_run_service(
    db: AsyncSession = Depends(get_db),
) -> RunService:
    """Build RunService with workflow persistence for run creation."""

    return build_run_service(db)


def get_run_query_service() -> RunService:
    """Build RunService for PostgreSQL-backed run queries and execution operations."""

    return build_run_service()


async def get_research_result_service(
    db: AsyncSession = Depends(get_db),
) -> ResearchResultService:
    return build_research_result_service(db)


def get_artifact_storage() -> LocalArtifactStorage:
    return LocalArtifactStorage()


def get_rate_limiter() -> RateLimiter:
    """Use a shared Redis limiter outside isolated tests."""

    if os.getenv("TESTING", "").lower() == "true":
        return NoopRateLimiter()
    return RedisRateLimiter()
