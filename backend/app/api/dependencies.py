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


def get_current_user_id(authorization: str | None = Header(None)) -> str:
    """Resolve the authenticated user, retaining a test-only compatibility user."""

    if os.getenv("TESTING", "").lower() == "true":
        return DEFAULT_USER_ID
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    service = build_auth_service()
    try:
        # This dependency only verifies token claims. Resource existence is
        # checked by the application service/repository when needed.
        from app.modules.identity.security import decode_access_token

        claims = decode_access_token(authorization.split(" ", 1)[1].strip(), service.signing_secret)
        return str(claims["sub"])
    except Exception as exc:
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


async def get_research_result_service(
    db: AsyncSession = Depends(get_db),
) -> ResearchResultService:
    return build_research_result_service(db)


def get_artifact_storage() -> LocalArtifactStorage:
    return LocalArtifactStorage()
