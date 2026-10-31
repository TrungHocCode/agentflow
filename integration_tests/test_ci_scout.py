"""CI-P7 Scout discovery round trip on explicitly disposable PostgreSQL/Redis targets only."""

import asyncio
import json
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from integration_tests.environment import require_integration_environment

require_integration_environment()

from app.db.init_db import init_tables, seed_defaults
from app.db.postgres_client import AsyncSessionLocal, engine
from app.db.redis_client import close_redis_connection
from app.infrastructure.postgres.intelligence_repository import PostgresIntelligenceRepository
from app.infrastructure.redis.rate_limiter import NoopRateLimiter
from app.modules.competitive_intelligence.models import CreateWatchlist
from app.modules.competitive_intelligence.scout_discovery import ScoutDiscovery
from app.modules.competitive_intelligence.service import IntelligenceService

PROMPT = "I make TaskFlow, a team task manager. Track pricing of Todoist and ClickUp."


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class StubSearchTool:
    """Stable search fixture; stage B answers with the requested site only."""

    def __call__(self, queries, max_results):
        results = []
        for query in queries:
            if query.startswith("site:"):
                host = query.split()[0].removeprefix("site:")
                results += [
                    {"url": f"https://{host}/pricing", "title": f"{host} pricing",
                     "snippet": "Pricing page"},
                    {"url": "https://elsewhere.example/pricing", "title": "Off-site",
                     "snippet": "Wrong host"},
                ]
            else:
                results += [
                    {"url": "https://todoist.com/pricing", "title": "Todoist Pricing",
                     "snippet": "Todoist pricing"},
                    {"url": "https://clickup.com/pricing", "title": "ClickUp Pricing",
                     "snippet": "ClickUp pricing"},
                    {"url": "https://www.todoist.com/features", "title": "Todoist Features",
                     "snippet": "Todoist features"},
                ]
        return json.dumps({"data": {"results": results}})


class TestCIScoutRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = str(uuid4())
        self.session = AsyncSessionLocal()
        self.addAsyncCleanup(self.cleanup)
        self.service = IntelligenceService(
            PostgresIntelligenceRepository(), MagicMock(),
            scout=ScoutDiscovery(StubSearchTool()),
            rate_limiter=NoopRateLimiter(),
        )

    async def cleanup(self) -> None:
        await self.session.close()
        await close_redis_connection()
        await engine.dispose()

    async def test_discover_pick_create_round_trip(self) -> None:
        recommendation = await self.service.discover_competitors(PROMPT, self.owner)
        competitors = [product for product in recommendation.products if product.kind == "competitor"]
        self.assertEqual({product.official_website for product in competitors},
                         {"https://todoist.com/", "https://clickup.com/"})
        self.assertTrue(all(product.provenance == "web_search" for product in competitors))
        self.assertTrue(all(product.sources == [] for product in competitors))

        domains = [product.official_website for product in competitors]
        sources_by_domain = await self.service.discover_sources(domains, "pricing", self.owner)
        self.assertEqual(set(sources_by_domain), set(domains))
        for domain, sources in sources_by_domain.items():
            self.assertTrue(sources, domain)
            self.assertEqual([source.url for source in sources], [f"{domain}pricing"])

        watchlist = await self.service.create(CreateWatchlist.model_validate({
            "name": "Scout discovered watch",
            "description": "",
            "config": {
                "goal": "Track Todoist and ClickUp pricing against TaskFlow",
                "dimensions": ["pricing"],
                "comparison_criteria": [{"field": "price", "objective": "SMB affordability"}],
                "products": [
                    {"name": "TaskFlow", "kind": "own", "company": "TaskFlow Inc",
                     "official_website": "https://taskflow.example/", "sources": []},
                    *[
                        {"name": product.name, "kind": "competitor", "company": "",
                         "official_website": product.official_website,
                         "sources": [{"url": source.url, "kind": source.kind}
                                     for source in sources_by_domain[product.official_website]]}
                        for product in competitors
                    ],
                ],
            },
        }), self.owner)
        names = {product.name for product in watchlist.current_revision.config.products}
        self.assertIn("TaskFlow", names)
        self.assertEqual(len(watchlist.current_revision.config.products), 3)
        url_sets = {source.url for product in watchlist.current_revision.config.products
                    for source in product.sources}
        self.assertEqual(url_sets, {"https://todoist.com/pricing", "https://clickup.com/pricing"})
        self.assertEqual(watchlist.current_revision.approval_status, "unapproved")
