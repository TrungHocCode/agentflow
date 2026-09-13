"""Ports exposed by the catalog bounded context."""

from typing import List, Protocol

from app.modules.catalog.domain import AgentDefinition, ToolDefinition


class CatalogRepository(Protocol):
    async def list_agents(self, active_only: bool = True) -> List[AgentDefinition]:
        ...
    async def list_tools(self, active_only: bool = True) -> List[ToolDefinition]:
        ...


class CatalogQueryPort(Protocol):
    async def get_agent(self, name: str) -> AgentDefinition | None:
        ...

    async def get_tool(self, name: str) -> ToolDefinition | None:
        ...
