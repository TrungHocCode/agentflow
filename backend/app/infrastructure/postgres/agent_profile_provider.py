"""PostgreSQL adapter for execution-time agent profile resolution."""

from app.execution.agents.resolver import AgentProfile, AgentProfileProvider
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository


class PostgresAgentProfileProvider(AgentProfileProvider):
    """Translate persisted catalog definitions into execution profiles."""

    def __init__(self, repository: PostgresCatalogRepository | None = None) -> None:
        self.repository = repository or PostgresCatalogRepository()

    async def get_agent(self, identifier: str) -> AgentProfile | None:
        definition = await self.repository.get_agent(identifier)
        if definition is None:
            return None
        return AgentProfile(
            name=definition.name,
            system_prompt=definition.system_prompt,
            tool_names=definition.tool_names,
            runtime_name="worker",
        )
