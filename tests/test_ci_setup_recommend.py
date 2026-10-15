"""CI-P7 setup recommendations; deterministic fixtures, mocked planner, no network or model."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.api.dependencies import get_current_user_id, get_intelligence_service
from app.main import app
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.setup_recommend import (
    RecommendedProduct, RecommendedSource, SetupRecommendation,
)
from app.shared.errors import ValidationError

PROMPT = "I make TaskFlow, a team task manager. Track pricing of Todoist and ClickUp."


def recommendation_fixture() -> SetupRecommendation:
    return SetupRecommendation(
        products=[
            RecommendedProduct(name="TaskFlow", kind="own", company="TaskFlow Inc",
                               official_website="https://taskflow.example/",
                               sources=[], rationale="Declared by the user."),
            RecommendedProduct(name="Todoist", kind="competitor", company="Doist",
                               official_website="https://todoist.com/",
                               sources=[RecommendedSource(url="https://todoist.com/pricing", kind="pricing",
                                                          provenance="model_knowledge")],
                               rationale="Direct competitor."),
        ],
        suggested_goal="Track Todoist pricing vs TaskFlow",
        suggested_dimensions=["pricing"],
        suggested_criteria=[{"field": "price", "objective": "SMB affordability"}],
        warnings=["All entries are unverified drafts."])


class TestRecommend(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.planner = AsyncMock()
        self.planner.recommend = AsyncMock(return_value=recommendation_fixture())
        self.service = IntelligenceService(AsyncMock(), MagicMock(), planner=self.planner)

    async def test_happy_path_returns_candidates_unapproved(self) -> None:
        recommendation = await self.service.recommend(PROMPT, "owner")
        self.assertEqual(len(recommendation.products), 2)
        self.assertEqual(recommendation.products[1].sources[0].provenance, "model_knowledge")

    async def test_invalid_urls_dropped_with_warnings(self) -> None:
        draft = recommendation_fixture()
        draft.products[1].sources.append(
            RecommendedSource(url="http://127.0.0.1/pricing", kind="pricing", provenance="model_knowledge"))
        self.planner.recommend = AsyncMock(return_value=draft)
        recommendation = await self.service.recommend(PROMPT, "owner")
        urls = [source.url for product in recommendation.products for source in product.sources]
        self.assertNotIn("http://127.0.0.1/pricing", urls)
        self.assertTrue(any("Dropped" in warning for warning in recommendation.warnings))

    async def test_planner_unavailable_fails_visibly(self) -> None:
        service = IntelligenceService(AsyncMock(), MagicMock(), planner=None)
        with self.assertRaises(ValidationError):
            await service.recommend(PROMPT, "owner")

    async def test_recommend_endpoint(self) -> None:
        app.dependency_overrides[get_current_user_id] = lambda: "owner"
        app.dependency_overrides[get_intelligence_service] = lambda: self.service
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                response = await client.post("/api/v1/watchlists/recommendations", json={"prompt": PROMPT})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()["products"]), 2)
                empty = await client.post("/api/v1/watchlists/recommendations", json={"prompt": ""})
                self.assertEqual(empty.status_code, 422)
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
