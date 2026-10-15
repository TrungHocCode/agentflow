"""CI-P9 timeline and structured Q&A; deterministic fixtures, mocked services, no network."""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.api.dependencies import get_current_user_id, get_history_qa
from app.main import app
from app.modules.competitive_intelligence.history_qa import HistoryQA, classify_intent
from app.modules.competitive_intelligence.timeline_contracts import TimelineEvent
from app.modules.competitive_intelligence.timeline_service import TimelineService

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def event_fixture(kind="change_detected", at=None, summary="modified in section 'Pricing'") -> TimelineEvent:
    return TimelineEvent(id=uuid4(), at=at or NOW, kind=kind, source_id=uuid4(), summary=summary,
                         snapshot_ids=[uuid4()])


class TestClassify(unittest.TestCase):
    def test_intent_routing(self) -> None:
        self.assertEqual(classify_intent("When did they drop the annual discount?"), "temporal")
        self.assertEqual(classify_intent("Show me the history of pricing changes"), "temporal")
        self.assertEqual(classify_intent("Summarize everything from Q3"), "summary")
        self.assertEqual(classify_intent("What is the current Pro price?"), "factual")
        self.assertEqual(classify_intent("Hi"), "keyword")


class TestTimeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.snapshots, self.investigations, self.briefs = AsyncMock(), AsyncMock(), AsyncMock()
        self.intelligence = AsyncMock()
        self.timeline = TimelineService(self.snapshots, self.investigations, self.briefs, self.intelligence)

    async def test_merges_orders_and_filters(self) -> None:
        run = MagicMock()
        run.run_id = str(uuid4())
        self.intelligence.get = AsyncMock(return_value=MagicMock())
        self.intelligence.runs = AsyncMock(return_value=MagicMock(items=[run]))
        old = event_fixture(at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.snapshots.list_comparisons = AsyncMock(return_value=[])
        self.snapshots.list_candidates = AsyncMock(return_value=[])
        self.investigations.list_rounds = AsyncMock(return_value=[])
        brief = MagicMock()
        brief.created_at = datetime(2026, 10, 8, tzinfo=timezone.utc)
        brief.outcome, brief.summary, brief.id = "changes_detected", "Run compared 1 source.", uuid4()
        brief.source_coverage = []
        self.briefs.list_briefs = AsyncMock(return_value=([brief], 1))
        events = await self.timeline.competitor_timeline("owner", uuid4(), None, 50)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "brief_built")


class TestHistoryQA(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.timeline, self.snapshots, self.intelligence = AsyncMock(), AsyncMock(), AsyncMock()
        self.qa = HistoryQA(self.timeline, self.snapshots, self.intelligence)
        self.event = event_fixture(summary="modified in section 'Pricing': Pro costs $10 → $12")
        self.timeline.competitor_timeline = AsyncMock(return_value=[self.event])
        self.snapshots.read_content = AsyncMock(return_value=MagicMock(
            start_offset=0, text="Pro plan costs $12 per month."))

    async def test_temporal_answers_with_citations(self) -> None:
        answer = await self.qa.ask("owner", uuid4(), "When did the Pricing section change?")
        self.assertEqual((answer.intent, answer.verdict), ("temporal", "answered"))
        self.assertTrue(answer.citations)
        self.assertIn("2026-10-09", answer.text)

    async def test_unknown_returns_not_observed_without_invention(self) -> None:
        self.timeline.competitor_timeline = AsyncMock(return_value=[])
        self.intelligence.get = AsyncMock(return_value=MagicMock(
            current_revision=MagicMock(config=MagicMock(products=[]))))
        answer = await self.qa.ask("owner", uuid4(), "Is the competitor bankrupt?")
        self.assertEqual(answer.verdict, "not_observed")
        self.assertEqual(answer.citations, [])

    async def test_summary_without_briefs(self) -> None:
        self.timeline.briefs = AsyncMock()
        self.timeline.briefs.list_briefs = AsyncMock(return_value=([], 0))
        answer = await self.qa.ask("owner", uuid4(), "Summarize everything")
        self.assertEqual((answer.intent, answer.verdict), ("summary", "not_observed"))

    async def test_ask_endpoint(self) -> None:
        app.dependency_overrides[get_current_user_id] = lambda: "owner"
        app.dependency_overrides[get_history_qa] = lambda: self.qa
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                response = await client.post(f"/api/v1/watchlists/{uuid4()}/ask",
                                             json={"question": "When did Pricing change?"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["verdict"], "answered")
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
