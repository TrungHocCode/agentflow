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
from app.modules.competitive_intelligence.scout_discovery import ScoutDiscovery
from app.modules.competitive_intelligence.service import IntelligenceService
from app.shared.errors import RateLimitExceeded

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
