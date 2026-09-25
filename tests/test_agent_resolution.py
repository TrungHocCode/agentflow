import os
import sys
import unittest
from unittest.mock import MagicMock

from langchain_core.language_models import BaseChatModel

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.agents.resolver import (
    AgentProfile,
    AgentResolver,
)
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
            {
                "web_search",
                "web_search_batch",
                "news_crawler",
                "news_crawler_batch",
                "http_request",
            },
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

    async def test_plan_validation_rejects_tools_outside_the_agent_whitelist(self) -> None:
        resolver = AgentResolver()
        task = Task(
            id=1,
            node="source_researcher",
            status="pending",
            description="Collect sources",
            tool_names=["file_writer"],
        )

        with self.assertRaisesRegex(ValueError, "authorized"):
            await resolver.validate_plan([task])

    async def test_legacy_news_scraper_name_resolves_to_authorized_crawler(self) -> None:
        resolver = AgentResolver()
        resolved = await resolver.resolve(
            Task(
                id=1,
                node="source_researcher",
                status="running",
                description="Scrape the latest news",
                tool_names=["news_scraper"],
            )
        )

        self.assertEqual([tool.name for tool in resolved.tools], ["news_crawler"])
        self.assertEqual(resolved.denied_tool_names, ())

    async def test_news_scraper_alias_does_not_expand_profile_permissions(self) -> None:
        resolver = AgentResolver(
            fallback_profiles={
                "source_researcher": AgentProfile(
                    name="source_researcher",
                    system_prompt="Search sources.",
                    tool_names=["web_search"],
                )
            }
        )

        with self.assertRaisesRegex(ValueError, "no authorized registered tools"):
            await resolver.resolve(
                Task(
                    id=1,
                    node="source_researcher",
                    status="running",
                    description="Scrape the latest news",
                    tool_names=["news_scraper"],
                )
            )

    async def test_planner_tool_catalog_uses_active_profiles_and_registered_names(self) -> None:
        catalog = await AgentResolver().format_agent_tool_catalog()

        self.assertIn(
            "- source_researcher: web_search, web_search_batch, news_crawler, news_crawler_batch, http_request",
            catalog,
        )
        self.assertIn("news_scraper -> news_crawler", catalog)
        self.assertIn("Legacy names (do not use in plans)", catalog)

        class RestrictedProfileProvider:
            async def get_agent(self, identifier: str) -> AgentProfile | None:
                if identifier == "source_researcher":
                    return AgentProfile(
                        name="source_researcher",
                        system_prompt="Search sources.",
                        tool_names=["web_search"],
                    )
                return None

        active_catalog = await AgentResolver(
            provider=RestrictedProfileProvider()
        ).format_agent_tool_catalog()
        self.assertIn("- source_researcher: web_search\n", active_catalog)
        self.assertNotIn("- source_researcher: web_search, news_crawler", active_catalog)

    async def test_worker_prompt_lists_only_tools_authorized_for_this_task(self) -> None:
        resolver = AgentResolver()
        task = Task(
            id=1,
            node="source_researcher",
            status="running",
            description="Scrape the requested article",
            tool_names=["news_crawler"],
        )
        resolved = await resolver.resolve(task)
        agent = resolver.create_agent(
            resolved=resolved,
            llm=MagicMock(spec=BaseChatModel),
            task=task,
        )

        self.assertIn(
            "Authorized tools for this task (use these exact names only): news_crawler.",
            agent.system_prompt,
        )
        self.assertIn("up to eight selected URLs", agent.system_prompt)
        self.assertIn("batch success does not mean every source succeeded", agent.system_prompt)
        self.assertNotIn("news_scraper", agent.system_prompt)

    async def test_source_researcher_prompt_uses_batched_discovery_and_crawling(self) -> None:
        resolver = AgentResolver()
        task = Task(
            id=1,
            node="source_researcher",
            status="running",
            description="Research current open-source vector databases",
        )
        resolved = await resolver.resolve(task)
        agent = resolver.create_agent(
            resolved=resolved,
            llm=MagicMock(spec=BaseChatModel),
            task=task,
        )

        self.assertIn("Use web_search_batch with at most five queries", agent.system_prompt)
        self.assertIn("news_crawler_batch", agent.system_prompt)
        self.assertIn("up to eight selected URLs", agent.system_prompt)
        self.assertIn("compact evidence dossier for downstream agents", agent.system_prompt)
        for instruction in (
            "one entry per named subject",
            "A search snippet",
            "coverage status (COMPLETE or PARTIAL)",
            "one focused follow-up search batch",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, agent.system_prompt)

    async def test_synthesis_and_report_agents_get_tool_capability_guidance(self) -> None:
        resolver = AgentResolver()
        cases = [
            (
                "synthesis_agent",
                "text_summarizer is an extractive sentence sampler",
            ),
            (
                "report_agent",
                "markdown_report_generator only renders and saves",
            ),
        ]

        for index, (role, expected_guidance) in enumerate(cases, start=1):
            task = Task(
                id=index,
                node=role,
                status="running",
                description="Process the research evidence",
            )
            resolved = await resolver.resolve(task)
            agent = resolver.create_agent(
                resolved=resolved,
                llm=MagicMock(spec=BaseChatModel),
                task=task,
            )

            self.assertIn(expected_guidance, agent.system_prompt)

        synthesis_task = Task(
            id=1,
            node="synthesis_agent",
            status="running",
            description="Compare three named models using the research dossier",
        )
        synthesis_profile = await resolver.resolve(synthesis_task)
        synthesis_agent = resolver.create_agent(
            resolved=synthesis_profile,
            llm=MagicMock(spec=BaseChatModel),
            task=synthesis_task,
        )
        self.assertIn("Preserve every requested subject", synthesis_agent.system_prompt)
        self.assertIn("unsupported prose instead of source-backed observations", synthesis_agent.system_prompt)

    async def test_report_agent_prompt_requires_analysis_and_evidence_discipline(self) -> None:
        resolver = AgentResolver()
        task = Task(
            id=1,
            node="report_agent",
            status="running",
            description="Analyze the research evidence and write the final report",
        )
        resolved = await resolver.resolve(task)
        agent = resolver.create_agent(
            resolved=resolved,
            llm=MagicMock(spec=BaseChatModel),
            task=task,
        )

        for instruction in (
            "Do not merely reorder, list, or paraphrase crawler output.",
            "Compare independent sources",
            "Distinguish observed facts from reasoned inferences",
            "Cite important factual claims inline with exact supplied source URLs",
            "When upstream research marks coverage as partial",
            "instead of manufacturing depth",
            "return a one-line completion record",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, agent.system_prompt)

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
