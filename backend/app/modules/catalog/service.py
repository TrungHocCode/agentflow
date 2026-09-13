"""Application service for catalog queries."""

from typing import List

from app.modules.catalog.domain import AgentDefinition, ToolDefinition
from app.modules.catalog.ports import CatalogRepository


class CatalogService:
    """Coordinates catalog use cases without knowing persistence details."""

    def __init__(self, repository: CatalogRepository):
        self.repository = repository

    async def list_agents(self, active_only: bool = True) -> List[AgentDefinition]:
        return await self.repository.list_agents(active_only=active_only)

    async def list_tools(self, active_only: bool = True) -> List[ToolDefinition]:
        return await self.repository.list_tools(active_only=active_only)
