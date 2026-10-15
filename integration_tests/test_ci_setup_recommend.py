"""CI-P7 setup recommendation gates on explicitly disposable PostgreSQL/Redis targets only."""

import asyncio
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from integration_tests.environment import require_integration_environment

require_integration_environment()

from app.db.init_db import init_tables, seed_defaults
from app.db.postgres_client import AsyncSessionLocal, engine
from app.db.redis_client import close_redis_connection, get_redis
from app.infrastructure.postgres.intelligence_repository import PostgresIntelligenceRepository
from app.modules.competitive_intelligence.models import CreateWatchlist
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.setup_recommend import (
    RecommendedProduct, RecommendedSource, SetupRecommendation,
)


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class StubPlanner:
    async def propose(self, messages):  # pragma: no cover - unused in this module
        raise AssertionError("propose must not be called here")

    async def recommend(self, prompt: str) -> SetupRecommendation:
        assert "TaskFlow" in prompt
        return SetupRecommendation(
            products=[
                RecommendedProduct(name="TaskFlow", kind="own", company="TaskFlow Inc",
                                   official_website="https://taskflow.example/", sources=[],
                                   rationale="Declared by the user."),
                RecommendedProduct(name="Todoist", kind="competitor", company="Doist",
                                   official_website="https://todoist.com/",
                                   sources=[RecommendedSource(url="https://todoist.com/pricing",
                                                              kind="pricing",
                                                              provenance="model_knowledge")],
                                   rationale="Direct competitor."),
            ],
            suggested_goal="Track Todoist pricing vs TaskFlow",
            suggested_dimensions=["pricing"],
            suggested_criteria=[{"field": "price", "objective": "SMB affordability"}],
            warnings=["All entries are unverified drafts."])


class TestCIRealSetupRecommend(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = str(uuid4())
        self.session = AsyncSessionLocal()
        self.addAsyncCleanup(self.cleanup)
        self.service = IntelligenceService(PostgresIntelligenceRepository(), MagicMock(),
                                           planner=StubPlanner())

    async def cleanup(self) -> None:
        await self.session.close()
        await close_redis_connection()
        await engine.dispose()

    async def test_recommend_pick_create_round_trip(self) -> None:
        recommendation = await self.service.recommend("I make TaskFlow. Track Todoist pricing.", self.owner)
        self.assertEqual(len(recommendation.products), 2)
        picked = [product for product in recommendation.products
                  if product.kind == "competitor" or product.kind == "own"]
        watchlist = await self.service.create(CreateWatchlist.model_validate({
            "name": "Recommended watch",
            "description": "",
            "config": {
                "goal": recommendation.suggested_goal,
                "dimensions": recommendation.suggested_dimensions,
                "workflow_version_id": None,
                "comparison_criteria": [criterion.model_dump() for criterion in recommendation.suggested_criteria],
                "products": [
                    {"name": product.name, "kind": product.kind, "company": product.company,
                     "official_website": product.official_website,
                     "sources": [{"url": source.url, "kind": source.kind} for source in product.sources]}
                    for product in picked
                ],
            },
        }), self.owner)
        names = {product.name for product in watchlist.current_revision.config.products}
        self.assertEqual(names, {"TaskFlow", "Todoist"})
        rival = next(product for product in watchlist.current_revision.config.products
                     if product.kind == "competitor")
        self.assertEqual([source.url for source in rival.sources], ["https://todoist.com/pricing"])
        self.assertEqual(watchlist.current_revision.approval_status, "unapproved")
