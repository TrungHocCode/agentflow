"""CI-P7 search-grounded Scout discovery; deterministic stubs, no network or model."""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.api.dependencies import get_current_user_id, get_intelligence_service
from app.core.config import settings
from app.infrastructure.redis.rate_limiter import NoopRateLimiter, RedisRateLimiter
from app.main import app
from app.modules.competitive_intelligence.scout_discovery import ScoutDiscovery, _product_name
from app.modules.competitive_intelligence.service import IntelligenceService
from app.shared.errors import DependencyUnavailableError, RateLimitExceeded

PROMPT = "I make TaskFlow, a team task manager. Track pricing of Todoist and ClickUp."


def batch_payload(results):
    return json.dumps({"data": {"results": results}})


class StubBatchTool:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, queries, max_results):
        self.calls.append((list(queries), max_results))
        return batch_payload(self.results)


COMPETITOR_RESULTS = [
    {"url": "https://www.todoist.com/pricing", "title": "Todoist Pricing",
     "snippet": "Todoist pricing plans for teams"},
    {"url": "https://todoist.com/features", "title": "Todoist Features",
     "snippet": "Todoist features and capabilities"},
    {"url": "https://clickup.com/pricing", "title": "ClickUp Pricing",
     "snippet": "ClickUp pricing plans"},
    {"url": "https://unrelated.example/blog", "title": "Unrelated article",
     "snippet": "Nothing about the requested products"},
    {"url": "https://www.clickup.com/?utm_source=search", "title": "ClickUp",
     "snippet": "ClickUp workspace"},
]

SOURCE_RESULTS = [
    {"url": "https://todoist.com/pricing", "title": "Todoist Pricing",
     "snippet": "Todoist pricing page"},
    {"url": "https://app.todoist.com/plans", "title": "Todoist Plans",
     "snippet": "Todoist app plans"},
    {"url": "https://todoist.com/pricing", "title": "Todoist Pricing",
     "snippet": "Duplicate result"},
    {"url": "https://evil.example/pricing", "title": "Off-site",
     "snippet": "Not the picked domain"},
    {"url": "https://todoist.com:8443/pricing", "title": "Bad port",
     "snippet": "Non-standard port"},
    {"url": "https://todoist.com/pricing#plans", "title": "Fragment",
     "snippet": "Fragments are not valid source URLs"},
]


NOTE_PROMPT = "I make NoteNest, a note-taking app for small teams. Track pricing of Notion and Obsidian."

# Real results recorded from a trial search for NOTE_PROMPT.
NOTE_RESULTS = [
    {"url": "https://www.notion.com/pricing", "title": "Notion Pricing Plans: Free, Plus, Business, & Enterprise.",
     "snippet": "Learn more about our different pricing plans."},
    {"url": "https://notenest.com/pricing", "title": "NoteNest Pricing",
     "snippet": "Discover affordable pricing plans for NoteNest."},
    {"url": "https://startupcost.ai/notion-vs-obsidian", "title": "Notion vs Obsidian",
     "snippet": "Side-by-side true cost comparison."},
    {"url": "https://tryeyrie.com/blog/obsidian", "title": "The 9 best Obsidian alternatives in 2026 (honestly compared)",
     "snippet": "Nine tools compared by price."},
    {"url": "https://myflexnote.com/blog/notion", "title": "The 10 Best Notion Alternatives in 2026 (Tested and Ranked)",
     "snippet": "Ranked by why people leave."},
    {"url": "https://productivetemply.com/obsidian-vs-notion",
     "title": "Obsidian vs Notion: Honest comparison in 2026 (I tried both)", "snippet": "Compared."},
    {"url": "https://builder.io/blog/notion", "title": "The 12 Best Notion Alternatives in 2026",
     "snippet": "Compare the 12 best."},
    {"url": "https://obsidian.md/pricing", "title": "Obsidian", "snippet": "Commercial license details."},
    {"url": "https://thedigitalprojectmanager.com/obsidian", "title": "10 Best Obsidian Alternatives for Note-Taking in 2026",
     "snippet": "Top alternatives."},
]


def refused_batch(*_args):
    """The batch tool's real shape when the provider answers HTTP 202 to every query."""
    return json.dumps({"ok": False, "status": "empty", "error": {"code": "all_queries_failed"},
                       "data": {"results": [], "queries": [{"query": "q", "status": "http_error", "results": [],
                                                            "error": {"code": "unexpected_search_status"}}]}})


def no_results_batch(*_args):
    return json.dumps({"ok": False, "status": "empty", "error": {"code": "all_queries_failed"},
                       "data": {"results": [], "queries": [{"query": "q", "status": "empty", "results": []}]}})


class TestScoutProfile(unittest.TestCase):
    def test_scout_profile_is_search_only_and_not_a_round_role(self) -> None:
        from app.execution.agents.resolver import DEFAULT_AGENT_PROFILES
        from app.modules.competitive_intelligence.investigation_contracts import CI_ROLE_TOOLS
        from app.modules.competitive_intelligence.service import SAFE_ROLES

        profile = DEFAULT_AGENT_PROFILES["scout"]
        self.assertEqual(set(profile.tool_names), {"web_search", "web_search_batch"})
        self.assertEqual(profile.runtime_name, "worker")
        self.assertNotIn("scout", CI_ROLE_TOOLS)
        self.assertNotIn("scout", SAFE_ROLES)


class TestScoutStageA(unittest.IsolatedAsyncioTestCase):
    async def test_ranks_and_dedupes_domains_with_web_search_provenance(self) -> None:
        tool = StubBatchTool(COMPETITOR_RESULTS)
        products = await ScoutDiscovery(tool).stage_a(PROMPT)

        self.assertEqual(len(tool.calls), 1)
        queries, max_results = tool.calls[0]
        self.assertLessEqual(len(queries), 2)
        self.assertEqual(max_results, 5)
        self.assertTrue(any("Todoist" in query for query in queries))

        websites = [product.official_website for product in products]
        self.assertEqual(len(websites), 3)
        self.assertEqual(len(set(websites)), 3)
        self.assertIn("https://todoist.com/", websites)
        self.assertIn("https://clickup.com/", websites)
        self.assertNotIn("https://www.todoist.com/", websites)
        self.assertEqual({product.provenance for product in products}, {"web_search"})
        self.assertTrue(all(product.kind == "competitor" for product in products))
        self.assertTrue(all(product.sources == [] for product in products))
        self.assertIn("web search", products[0].rationale)
        self.assertEqual(websites[0], "https://todoist.com/")
        self.assertEqual(websites[1], "https://clickup.com/")
        self.assertEqual(websites[-1], "https://unrelated.example/")

    async def test_keeps_real_products_and_drops_comparison_articles(self) -> None:
        scout = ScoutDiscovery(StubBatchTool(NOTE_RESULTS))
        products = await scout.stage_a(NOTE_PROMPT)

        self.assertEqual({product.official_website for product in products},
                         {"https://notion.com/", "https://notenest.com/", "https://obsidian.md/"})
        self.assertTrue(any("Skipped 6 comparison" in warning for warning in scout.warnings))

    async def test_uses_the_named_product_and_recognizes_your_own(self) -> None:
        products = await ScoutDiscovery(StubBatchTool(NOTE_RESULTS)).stage_a(NOTE_PROMPT)
        by_site = {product.official_website: product for product in products}

        self.assertEqual(by_site["https://notion.com/"].name, "Notion")
        self.assertEqual(by_site["https://obsidian.md/"].name, "Obsidian")
        self.assertEqual(by_site["https://notenest.com/"].kind, "own")
        self.assertEqual(by_site["https://notion.com/"].kind, "competitor")
        self.assertTrue(all(product.suggested for product in products))

    async def test_searches_each_named_competitor_but_not_your_own_product(self) -> None:
        tool = StubBatchTool(NOTE_RESULTS)
        await ScoutDiscovery(tool).stage_a(NOTE_PROMPT)
        self.assertEqual(tool.calls[0][0], ["Notion pricing page", "Obsidian pricing page"])

    async def test_sentence_words_do_not_use_up_named_lookups(self) -> None:
        tool = StubBatchTool(NOTE_RESULTS)
        await ScoutDiscovery(tool).stage_a("Please watch Notion, Asana, Trello and Jira.")
        self.assertEqual(tool.calls[0][0], ["Notion official website", "Asana official website",
                                            "Trello official website", "Jira official website"])

    async def test_names_beyond_the_lookup_limit_are_reported(self) -> None:
        scout = ScoutDiscovery(StubBatchTool(NOTE_RESULTS))
        await scout.stage_a("Compare Alpha, Bravo, Charlie, Delta, Echo and Foxtrot.")
        self.assertEqual(len(scout.search_tool_caller.calls[0][0]), 5)
        self.assertTrue(any("Foxtrot" in warning for warning in scout.warnings))

    async def test_dotted_product_names_match_their_domain(self) -> None:
        results = [{"url": "https://monday.com/pricing", "title": "Best work platform compared", "snippet": "Plans"},
                   {"url": "https://cal.com/pricing", "title": "Cal.com pricing", "snippet": "Plans"}]
        products = await ScoutDiscovery(StubBatchTool(results)).stage_a("I build Cal.com. Track Monday.com pricing.")
        by_site = {product.official_website: product for product in products}

        self.assertEqual(by_site["https://monday.com/"].name, "Monday.com")
        self.assertTrue(by_site["https://monday.com/"].suggested)
        self.assertEqual(by_site["https://monday.com/"].kind, "competitor")
        self.assertEqual(by_site["https://cal.com/"].kind, "own")

    async def test_unnamed_candidates_are_not_suggested(self) -> None:
        results = [{"url": "https://rival.example/", "title": "Rival - Team tasks", "snippet": "Tasks"}]
        products = await ScoutDiscovery(StubBatchTool(results)).stage_a(PROMPT)
        self.assertEqual(products[0].name, "Rival")
        self.assertFalse(products[0].suggested)

    def test_product_name_drops_page_words_and_taglines(self) -> None:
        self.assertEqual(_product_name("Acme Pricing Plans: Free and Pro", "acme.com"), "Acme")
        self.assertEqual(_product_name("Acme | Team chat", "acme.com"), "Acme")
        self.assertEqual(_product_name("Pricing", "acme.com"), "Acme")

    async def test_provider_refusal_is_an_error_not_an_empty_result(self) -> None:
        with self.assertRaises(DependencyUnavailableError) as caught:
            await ScoutDiscovery(refused_batch).stage_a(PROMPT)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.code, "search_provider_unavailable")

    async def test_search_that_found_nothing_is_not_a_provider_failure(self) -> None:
        scout = ScoutDiscovery(no_results_batch)
        self.assertEqual(await scout.stage_a(PROMPT), [])
        self.assertTrue(any("No web search results" in warning for warning in scout.warnings))

    async def test_empty_results_return_no_candidates_and_a_warning(self) -> None:
        scout = ScoutDiscovery(StubBatchTool([]))
        products = await scout.stage_a(PROMPT)
        self.assertEqual(products, [])
        self.assertTrue(any("No web search results" in warning for warning in scout.warnings))


class TestScoutStageB(unittest.IsolatedAsyncioTestCase):
    async def test_same_site_filter_dedupes_and_drops_invalid_urls(self) -> None:
        tool = StubBatchTool(SOURCE_RESULTS)
        scout = ScoutDiscovery(tool)
        found = await scout.stage_b(["todoist.com"], "pricing")

        self.assertEqual(list(found), ["todoist.com"])
        self.assertEqual(tool.calls[0][0], ["site:todoist.com pricing page"])
        urls = [source.url for source in found["todoist.com"]]
        self.assertEqual(urls, ["https://todoist.com/pricing", "https://app.todoist.com/plans"])
        self.assertTrue(all(source.kind == "pricing" for source in found["todoist.com"]))
        self.assertTrue(all(source.provenance == "web_search" for source in found["todoist.com"]))
        self.assertTrue(any("Dropped syntactically invalid URL" in warning for warning in scout.warnings))


class TestScoutRoundCharging(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def service(search):
        limiter = AsyncMock()
        return IntelligenceService(AsyncMock(), MagicMock(), scout=ScoutDiscovery(search), rate_limiter=limiter), limiter

    async def test_successful_round_keeps_its_charge(self) -> None:
        service, limiter = self.service(StubBatchTool(COMPETITOR_RESULTS))
        await service.discover_competitors(PROMPT, "owner")
        limiter.consume.assert_awaited_once()
        limiter.release.assert_not_awaited()

    async def test_refused_search_is_free_and_still_reports_the_failure(self) -> None:
        service, limiter = self.service(refused_batch)
        with self.assertRaises(DependencyUnavailableError):
            await service.discover_sources(["todoist.com"], "pricing", "owner")
        limiter.release.assert_awaited_once_with("ci-scout", "owner")

    async def test_empty_rounds_are_free_in_both_stages(self) -> None:
        service, limiter = self.service(StubBatchTool([]))
        await service.discover_competitors(PROMPT, "owner")
        self.assertEqual(await service.discover_sources(["todoist.com"], "pricing", "owner"), {"todoist.com": []})
        self.assertEqual(limiter.release.await_count, 2)

    async def test_failed_refund_does_not_replace_the_result(self) -> None:
        service, limiter = self.service(StubBatchTool([]))
        limiter.release.side_effect = ConnectionError("redis down")
        self.assertEqual((await service.discover_competitors(PROMPT, "owner")).products, [])

    def test_default_allowance_fits_several_wizard_passes(self) -> None:
        self.assertGreaterEqual(settings.CI_SCOUT_ROUNDS_PER_DAY, 6)

    async def test_redis_release_decrements_the_same_key(self) -> None:
        client = AsyncMock()
        client.eval.return_value = 1
        with patch("app.infrastructure.redis.rate_limiter.get_redis", AsyncMock(return_value=client)):
            limiter = RedisRateLimiter()
            await limiter.consume("ci-scout", "owner", 3, 86400)
            await limiter.release("ci-scout", "owner")
        self.assertEqual(client.eval.await_args_list[0].args[2], client.eval.await_args_list[1].args[2])


class TestScoutRoundGuard(unittest.IsolatedAsyncioTestCase):
    async def test_fourth_discover_round_is_rejected_for_one_owner(self) -> None:
        class FakeRedis:
            def __init__(self):
                self.counts = {}
                self.keys = []

            async def eval(self, script, key_count, key, window_seconds):
                self.keys.append(key)
                self.counts[key] = self.counts.get(key, 0) + 1
                return self.counts[key]

        client = FakeRedis()
        with patch("app.infrastructure.redis.rate_limiter.get_redis", AsyncMock(return_value=client)):
            service = IntelligenceService(
                AsyncMock(), MagicMock(),
                scout=ScoutDiscovery(StubBatchTool(COMPETITOR_RESULTS)),
                rate_limiter=RedisRateLimiter(),
            )
            owner = str(uuid4())
            with patch.object(settings, "CI_SCOUT_ROUNDS_PER_DAY", 3):
                for _ in range(3):
                    await service.discover_competitors(PROMPT, owner)
                with self.assertRaises(RateLimitExceeded):
                    await service.discover_competitors(PROMPT, owner)

        self.assertEqual(len(client.keys), 4)
        self.assertEqual(len(set(client.keys)), 1)
        self.assertIn(":ci-scout:", client.keys[0])
        self.assertNotIn(owner, client.keys[0])


class TestScoutEndpoints(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = IntelligenceService(
            AsyncMock(), MagicMock(),
            scout=ScoutDiscovery(StubBatchTool(COMPETITOR_RESULTS)),
            rate_limiter=NoopRateLimiter(),
        )

    async def test_discover_endpoints_return_candidates_and_validate_input(self) -> None:
        app.dependency_overrides[get_current_user_id] = lambda: "owner"
        app.dependency_overrides[get_intelligence_service] = lambda: self.service
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                competitors = await client.post("/api/v1/watchlists/discover-competitors",
                                                json={"prompt": PROMPT})
                self.assertEqual(competitors.status_code, 200, competitors.text)
                self.assertEqual(len(competitors.json()["products"]), 3)
                self.assertEqual(competitors.json()["products"][0]["provenance"], "web_search")

                empty = await client.post("/api/v1/watchlists/discover-competitors", json={"prompt": ""})
                self.assertEqual(empty.status_code, 422)

                sources = await client.post("/api/v1/watchlists/discover-sources",
                                            json={"domains": ["todoist.com"], "dimension": "pricing"})
                self.assertEqual(sources.status_code, 200, sources.text)
                self.assertIn("todoist.com", sources.json())

                no_domains = await client.post("/api/v1/watchlists/discover-sources",
                                               json={"domains": [], "dimension": "pricing"})
                self.assertEqual(no_domains.status_code, 422)

                bad_dimension = await client.post("/api/v1/watchlists/discover-sources",
                                                  json={"domains": ["todoist.com"], "dimension": "bogus"})
                self.assertEqual(bad_dimension.status_code, 422)
        finally:
            app.dependency_overrides.clear()

    async def test_discover_endpoints_require_authentication(self) -> None:
        app.dependency_overrides[get_intelligence_service] = lambda: self.service
        try:
            with patch.object(settings, "AUTH_MODE", "required"):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://test") as client:
                    response = await client.post("/api/v1/watchlists/discover-competitors",
                                                 json={"prompt": PROMPT})
                    self.assertEqual(response.status_code, 401)
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
