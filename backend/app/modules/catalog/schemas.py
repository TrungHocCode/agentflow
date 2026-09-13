"""Transport schemas for the Catalog bounded context."""

from app.modules.catalog.domain import AgentDefinition, ToolDefinition


AgentCatalogResponse = AgentDefinition
ToolCatalogResponse = ToolDefinition
