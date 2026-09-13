"""SQLAlchemy adapter for the Catalog bounded context."""

from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_catalog.models import AgentCatalogModel, ToolCatalogModel
from app.modules.catalog.domain import AgentDefinition, ToolDefinition
from app.modules.catalog.ports import CatalogRepository


class PostgresCatalogRepository(CatalogRepository):
    """Reads catalog records without exposing ORM models to application code."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_agents(self, active_only: bool = True) -> List[AgentDefinition]:
        statement = select(AgentCatalogModel)
        if active_only:
            statement = statement.where(AgentCatalogModel.is_active.is_(True))
        try:
            result = await self.session.execute(statement)
        except Exception:
            # Keep the existing local/test behavior when PostgreSQL is not running.
            return []
        return [AgentDefinition.model_validate(row) for row in result.scalars().all()]

    async def list_tools(self, active_only: bool = True) -> List[ToolDefinition]:
        statement = select(ToolCatalogModel)
        if active_only:
            statement = statement.where(ToolCatalogModel.is_active.is_(True))
        try:
            result = await self.session.execute(statement)
        except Exception:
            # Keep the existing local/test behavior when PostgreSQL is not running.
            return []
        return [ToolDefinition.model_validate(row) for row in result.scalars().all()]
