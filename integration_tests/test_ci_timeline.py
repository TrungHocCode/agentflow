"""CI-P9 real-adapter gates on explicitly disposable PostgreSQL/Redis targets only."""

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from uuid import UUID, uuid4
from unittest.mock import MagicMock

from integration_tests.environment import require_integration_environment

require_integration_environment()

from app.db.init_db import init_tables, seed_defaults
from app.db.postgres_client import AsyncSessionLocal, engine
from app.db.redis_client import close_redis_connection, get_redis
from app.infrastructure.artifacts.brief_blobs import BriefFileStore
from app.infrastructure.artifacts.snapshot_blobs import SnapshotFileStore
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository
from app.infrastructure.postgres.intelligence_brief_repository import PostgresBriefRepository
from app.infrastructure.postgres.intelligence_investigation_repository import PostgresInvestigationRepository
from app.infrastructure.postgres.intelligence_repository import PostgresIntelligenceRepository
from app.infrastructure.postgres.intelligence_snapshot_repository import PostgresSnapshotRepository
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository
from app.infrastructure.redis.run_queue import RedisRunCommandQueue
from app.modules.competitive_intelligence.brief_service import BriefService
from app.modules.competitive_intelligence.history_qa import HistoryQA
from app.modules.competitive_intelligence.investigation_service import InvestigationService
from app.modules.competitive_intelligence.models import CreateWatchlist, StartRunRequest, digest
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.snapshot_service import SnapshotService
from app.modules.competitive_intelligence.timeline_service import TimelineService
from app.modules.runs.service import RunService
from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.workflows.validator import validate_workflow_references
from app.shared.errors import ResourceNotFoundError
from sqlalchemy import text

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
PRICING_BODY = "# Pricing\n\nPro plan costs ${} per month, billed annually ex VAT.\n\n## Features\n\nSSO included.\n"


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestCIRealTimelineQA(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = str(uuid4())
        self.queue_key = f"agentflow:test:ci-qa:{uuid4()}"
        self.session = AsyncSessionLocal()
        self.addAsyncCleanup(self.cleanup)
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self.close_tmp)
        self.repository = PostgresIntelligenceRepository()
        snapshots = PostgresSnapshotRepository(blobs=SnapshotFileStore(root=self.tmp.name))
        self.snapshot_service = SnapshotService(snapshots)
        investigations = InvestigationService(PostgresInvestigationRepository(), snapshots)
        workflow_repo = PostgresWorkflowRepository(self.session)
        catalog = PostgresCatalogRepository(self.session)
        definition = await validate_workflow_references(normalize_workflow_definition({"tasks": [
            {"id": 1, "task_key": "collect", "node": "source_researcher", "tool_names": ["news_crawler"],
             "description": "Collect", "status": "pending"}]}), catalog)
        workflow = await workflow_repo.create("CI QA fixture", None, self.owner, definition)
        self.version = workflow.version_id
        self.runs = RunService(PostgresRunRepository(), workflow_repo, MagicMock(),
            command_queue=RedisRunCommandQueue(self.queue_key), catalog_repository=catalog)
        self.service = IntelligenceService(self.repository, self.runs)
        briefs = BriefService(PostgresBriefRepository(), snapshots, BriefFileStore(root=self.tmp.name),
                              self.runs)
        timeline = TimelineService(self.snapshot_service, investigations, briefs, self.service)
        self.qa = HistoryQA(timeline, self.snapshot_service, self.service)
        self.request = CreateWatchlist.model_validate({"name": "QA competitors", "config": {
            "goal": "Assess pricing changes", "dimensions": ["pricing"],
            "workflow_version_id": self.version,
            "comparison_criteria": [{"field": "price", "objective": "SMB affordability"}],
            "products": [{"name": "Ours", "kind": "own", "official_website": "https://ours.invalid/"},
                         {"name": "Rival", "kind": "competitor", "official_website": "https://rival.invalid/",
                          "sources": [{"url": "https://rival.invalid/pricing", "kind": "pricing"}]}]}})
        self.watchlist = await self.service.create(self.request, self.owner)
        revision = await self.service.approve(str(self.watchlist.id), str(self.watchlist.current_revision.id),
                                              self.version, self.owner)
        self.revision = revision
        self.source = revision.config.products[1].sources[0]
        self.config_version = digest(self.source)
        started = await self.service.start(str(self.watchlist.id),
            StartRunRequest(revision_id=revision.id, workflow_version_id=self.version), self.owner, None)
        self.run_id = UUID(started.run_id)
        await self._seed_history()

    async def cleanup(self) -> None:
        await self.session.close()
        await (await get_redis()).delete(self.queue_key)
        await close_redis_connection()
        await engine.dispose()

    async def close_tmp(self) -> None:
        self.tmp.cleanup()

    def capture_kwargs(self, text):
        return {"run_id": self.run_id, "source_id": self.source.id, "revision_id": self.revision.id,
                "owner_id": self.owner, "requested_url": self.source.url, "final_url": self.source.url,
                "http_status": 200, "fetch_status": "success", "reason_codes": [], "captured_text": text,
                "source_kind": "pricing", "language": "en", "region": "US",
                "source_config_version": self.config_version, "title": "Pricing",
                "content_type": "text/plain", "observed_at": NOW, "fetched_at": NOW}

    async def _seed_history(self) -> None:
        outcome, first = await self.snapshot_service.capture(**self.capture_kwargs(PRICING_BODY.format(10) * 4))
        comparison, _ = await self.snapshot_service.compare_source(
            self.owner, self.run_id, self.source.id, first.id, NOW)
        self.assertEqual(comparison.outcome, "baseline_created")
        # Advance the pointer directly; the single recorded comparison per run comes below.
        promoted = await self.snapshot_service.promote(first, str(self.run_id), None)
        self.assertEqual(promoted, "promoted")
        _, second = await self.snapshot_service.capture(**self.capture_kwargs(PRICING_BODY.format(12) * 4))
        comparison, candidates = await self.snapshot_service.compare_source(
            self.owner, self.run_id, self.source.id, second.id, NOW)
        self.assertEqual(comparison.outcome, "changed")
        finalized = await self.snapshot_service.finalize_source(
            self.owner, comparison, candidates, "completed", NOW, first.id)
        self.assertEqual(finalized.promotion, "promoted")
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(text("UPDATE runs SET status='completed' WHERE run_id=:id"),
                                      {"id": str(self.run_id)})
        await self.qa.timeline.briefs.build_brief(
            self.watchlist.id, self.revision.id, self.run_id, self.owner, NOW)

    async def test_timeline_orders_change_and_brief_events(self) -> None:
        events = await self.qa.timeline.competitor_timeline(
            self.owner, self.watchlist.id, None, 50)
        kinds = [event.kind for event in events]
        self.assertIn("change_detected", kinds)
        self.assertIn("brief_built", kinds)
        self.assertEqual(events, sorted(events, key=lambda event: event.at, reverse=True))

    async def test_factual_answer_cites_resolvable_span(self) -> None:
        answer = await self.qa.ask(self.owner, self.watchlist.id, "What does the Pro plan cost?")
        self.assertEqual(answer.verdict, "answered")
        self.assertTrue(answer.citations)
        for citation in answer.citations:
            content = await self.snapshot_service.read_content(
                str(citation.snapshot_id), self.owner, "normalized", citation.start_offset, 500)
            self.assertIn(content.text[:100], citation.excerpt[:100])

    async def test_unknown_and_foreign_owner_have_no_answers(self) -> None:
        answer = await self.qa.ask(self.owner, self.watchlist.id, "Is the competitor bankrupt?")
        self.assertEqual((answer.verdict, answer.citations), ("not_observed", []))
        with self.assertRaises(ResourceNotFoundError):
            await self.qa.ask(str(uuid4()), self.watchlist.id, "What does the Pro plan cost?")
