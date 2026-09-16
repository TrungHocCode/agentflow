import os
import sys
import unittest
from unittest.mock import MagicMock

from langchain_core.language_models import BaseChatModel

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.agents.resolver import AgentProfile, AgentResolver
from app.execution.state import Task
from app.execution.tools.registry import autodiscover_tools
from app.infrastructure.postgres.agent_profile_provider import PostgresAgentProfileProvider
from app.modules.catalog.domain import AgentDefinition


class FakeAgentProfileRepository:
    async def get_agent(self, identifier: str) -> AgentDefinition | None:
        if identifier not in {"source_researcher", "agent-source"}:
            return None
        return AgentDefinition(
            id="agent-source",
            name="source_researcher",
            system_prompt="Persist source URLs.",
            tool_names=["web_search", "news_crawler"],
        )


class TestAgentResolution(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        autodiscover_tools()

    async def test_legacy_node_resolves_to_role_profile_and_tool_whitelist(self) -> None:
        resolver = AgentResolver()
        resolved = await resolver.resolve(
            Task(
                id=1,
                node="worker",
                status="running",
                description="Crawl article sources from the web",
            )
        )

        self.assertEqual(resolved.profile.name, "source_researcher")
        self.assertEqual(
            {tool.name for tool in resolved.tools},
            {"web_search", "news_crawler", "http_request"},
        )

    async def test_task_tool_override_cannot_expand_profile_permissions(self) -> None:
        resolver = AgentResolver()
        resolved = await resolver.resolve(
            Task(
                id=1,
                node="source_researcher",
                status="running",
                description="Collect sources",
                tool_names=["web_search"],
            )
        )

        self.assertEqual([tool.name for tool in resolved.tools], ["web_search"])

    async def test_legacy_tool_name_in_agent_id_maps_to_role_profile(self) -> None:
        resolver = AgentResolver()
        resolved = await resolver.resolve(
            Task(
                id=1,
                node="news_crawler",
                agent_id="news_crawler",
                status="running",
                description="Crawl article sources",
            )
        )

        self.assertEqual(resolved.profile.name, "source_researcher")

    async def test_unknown_generic_task_uses_restricted_worker_profile(self) -> None:
        resolver = AgentResolver()
        resolved = await resolver.resolve(
            Task(
                id=1,
                node="worker",
                status="running",
                description="Perform a custom internal operation",
            )
        )

        self.assertEqual(resolved.profile.name, "worker")
        self.assertEqual(
            {tool.name for tool in resolved.tools},
            {"web_search", "file_reader"},
        )

    async def test_postgres_provider_maps_catalog_profile(self) -> None:
        provider = PostgresAgentProfileProvider(FakeAgentProfileRepository())
        profile = await provider.get_agent("agent-source")

        self.assertIsInstance(profile, AgentProfile)
        self.assertEqual(profile.name, "source_researcher")
        self.assertEqual(profile.tool_names, ["web_search", "news_crawler"])

    async def test_resolved_profile_can_create_registered_runtime_agent(self) -> None:
        resolver = AgentResolver()
        resolved = await resolver.resolve(
            Task(
                id=1,
                node="report_agent",
                status="running",
                description="Generate the report",
            )
        )
        agent = resolver.create_agent(
            resolved=resolved,
            llm=MagicMock(spec=BaseChatModel),
            task=Task(
                id=1,
                node="report_agent",
                status="running",
                description="Generate the report",
            ),
        )

        self.assertEqual(agent.name, "report_agent")
        self.assertEqual(agent.tools, resolved.tools)


if __name__ == "__main__":
    unittest.main()
