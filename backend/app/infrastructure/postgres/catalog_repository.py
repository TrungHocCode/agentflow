"""SQLAlchemy adapter for the Catalog bounded context."""

import os
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.models import AgentCatalogModel, ToolCatalogModel
from app.modules.catalog.domain import AgentDefinition, ToolDefinition
from app.modules.catalog.ports import CatalogRepository
from app.shared.errors import PersistenceError


class PostgresCatalogRepository(CatalogRepository):
    """Reads catalog records without exposing ORM models to application code."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @property
    def use_memory(self) -> bool:
        """Keep the dependency-free test path explicit and test-only."""

        return os.getenv("TESTING", "").lower() == "true"

    async def list_agents(self, active_only: bool = True) -> List[AgentDefinition]:
        if self.use_memory:
            return []
        statement = select(AgentCatalogModel)
        if active_only:
            statement = statement.where(AgentCatalogModel.is_active.is_(True))
        try:
            result = await self.session.execute(statement)
        except Exception as exc:
            raise PersistenceError("Could not list agent catalog.") from exc
        return [AgentDefinition.model_validate(row) for row in result.scalars().all()]

    async def list_tools(self, active_only: bool = True) -> List[ToolDefinition]:
        if self.use_memory:
            return []
        statement = select(ToolCatalogModel)
        if active_only:
            statement = statement.where(ToolCatalogModel.is_active.is_(True))
        try:
            result = await self.session.execute(statement)
        except Exception as exc:
            raise PersistenceError("Could not list tool catalog.") from exc
        return [ToolDefinition.model_validate(row) for row in result.scalars().all()]
