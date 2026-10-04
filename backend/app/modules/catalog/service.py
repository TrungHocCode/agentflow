"""Application service for catalog queries."""

from typing import List

from app.core.config import settings
from app.modules.catalog.domain import AgentDefinition, ToolDefinition
from app.modules.catalog.ports import CatalogRepository


class CatalogService:
    """Coordinates catalog use cases without knowing persistence details."""

    def __init__(self, repository: CatalogRepository):
        self.repository = repository

    async def list_agents(self, active_only: bool = True) -> List[AgentDefinition]:
        agents = await self.repository.list_agents(active_only=active_only)
        available_names = {tool.name for tool in await self.list_tools(active_only=True) if tool.is_available}
        result = []
        for agent in agents:
            available = []
            blocked = []
            for tool_name in agent.tool_names:
                if tool_name not in available_names:
                    blocked.append(tool_name)
                else:
                    available.append(tool_name)
            result.append(
                agent.model_copy(
                    update={
                        "available_tool_names": available,
                        "blocked_tool_names": blocked,
                    }
                )
            )
        return result

    async def list_tools(self, active_only: bool = True) -> List[ToolDefinition]:
        tools = await self.repository.list_tools(active_only=active_only)
        result = []
        for tool in tools:
            disabled_reason = self._disabled_reason(tool.name) or tool.unavailable_reason
            result.append(
                tool.model_copy(
                    update={
                        "is_available": tool.is_available and disabled_reason is None,
                        "unavailable_reason": disabled_reason,
                    }
                )
            )
        return result

    @staticmethod
    def _disabled_reason(tool_name: str) -> str | None:
        if tool_name == "python_executor" and not settings.ENABLE_UNSANDBOXED_PYTHON_EXECUTION:
            return "Disabled by deployment policy: this executor is not an OS sandbox."
        if tool_name == "email_sender" and not settings.ENABLE_EXTERNAL_SIDE_EFFECT_TOOLS:
            return "Disabled by deployment policy: external side effects are off by default."
        return None
