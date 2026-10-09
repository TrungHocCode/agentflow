"""CI-P5 real-adapter gates on explicitly disposable PostgreSQL/Redis targets only."""

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
from unittest.mock import MagicMock

from integration_tests.environment import require_integration_environment

require_integration_environment()

from alembic import command
from alembic.config import Config
from sqlalchemy import text

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
from app.modules.competitive_intelligence.investigation_service import InvestigationService
from app.modules.competitive_intelligence.models import CreateWatchlist, StartRunRequest, digest
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.snapshot_service import SnapshotService
from app.modules.runs.service import RunService
from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.workflows.validator import validate_workflow_references
from app.shared.errors import ResourceNotFoundError

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
PRICING_BODY = "# Pricing\n\nPro plan costs ${} per month, billed annually ex VAT.\n\n## Features\n\nSSO included.\n"


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestCIRealBriefs(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = str(uuid4())
        self.queue_key = f"agentflow:test:ci-brief:{uuid4()}"
        self.session = AsyncSessionLocal()
        self.addAsyncCleanup(self.cleanup)
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self.close_tmp)
        self.repository = PostgresIntelligenceRepository()
        self.snapshots = PostgresSnapshotRepository(blobs=SnapshotFileStore(root=self.tmp.name))
        self.snapshot_service = SnapshotService(self.snapshots)
        self.rounds = PostgresInvestigationRepository()
        self.investigation = InvestigationService(self.rounds, self.snapshots)
        workflow_repo = PostgresWorkflowRepository(self.session)
        catalog = PostgresCatalogRepository(self.session)
        definition = await validate_workflow_references(normalize_workflow_definition({"tasks": [
            {"id": 1, "task_key": "collect", "node": "source_researcher", "tool_names": ["news_crawler"],
             "description": "Collect approved public pricing", "status": "pending"}]}), catalog)
        workflow = await workflow_repo.create("CI brief fixture", None, self.owner, definition)
        self.version = workflow.version_id
        self.runs = RunService(PostgresRunRepository(), workflow_repo, MagicMock(),
            command_queue=RedisRunCommandQueue(self.queue_key), catalog_repository=catalog)
        self.service = IntelligenceService(self.repository, self.runs)
        self.briefs = BriefService(PostgresBriefRepository(), self.snapshots,
                                   BriefFileStore(root=self.tmp.name), self.runs)
        self.request = CreateWatchlist.model_validate({"name": "Brief competitors", "config": {
            "goal": "Assess pricing changes for small teams", "dimensions": ["pricing"],
            "workflow_version_id": self.version, "comparison_criteria": [
                {"field": "price", "objective": "SMB affordability"}],
            "products": [{"name": "Ours", "kind": "own", "official_website": "https://ours.invalid/",
                "profile": {"facts": [{"field": "price", "value": "$10/month"}], "target_segments": ["SMB"]}},
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

    async def cleanup(self) -> None:
        await self.session.close()
        await (await get_redis()).delete(self.queue_key)
        await close_redis_connection()
        await engine.dispose()

    async def close_tmp(self) -> None:
        self.tmp.cleanup()

    def capture_kwargs(self, run_id, text, **overrides):
        values = {"run_id": run_id, "source_id": self.source.id, "revision_id": self.revision.id,
                  "owner_id": self.owner, "requested_url": self.source.url, "final_url": self.source.url,
                  "http_status": 200, "fetch_status": "success", "reason_codes": [], "captured_text": text,
                  "source_kind": "pricing", "language": "en", "region": "US",
                  "source_config_version": self.config_version, "title": "Pricing",
                  "content_type": "text/plain", "observed_at": NOW, "fetched_at": NOW}
        values.update(overrides)
        return values

    async def complete_run(self, run_id: UUID) -> None:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(text("UPDATE runs SET status='completed' WHERE run_id=:id"),
                                      {"id": str(run_id)})

    async def compare_and_finalize(self, run_id: UUID, before: str, after: str):
        outcome, first = await self.snapshot_service.capture(**self.capture_kwargs(run_id, before * 4))
        comparison, _ = await self.snapshot_service.compare_source(
            self.owner, run_id, self.source.id, first.id, NOW)
        self.assertEqual(comparison.outcome, "baseline_created")
        # Advance the pointer directly; each (run, source) comparison is recorded exactly once below.
        promoted = await self.snapshots.promote_baseline(first, str(run_id), None)
        self.assertEqual(promoted, "promoted")
        _, second = await self.snapshot_service.capture(**self.capture_kwargs(run_id, after * 4))
        comparison, candidates = await self.snapshot_service.compare_source(
            self.owner, run_id, self.source.id, second.id, NOW)
        finalized = await self.snapshot_service.finalize_source(
            self.owner, comparison, candidates, "completed", NOW, first.id)
        return finalized, candidates

    async def test_build_changes_detected_brief_with_download(self) -> None:
        finalized, candidates = await self.compare_and_finalize(self.run_id, PRICING_BODY.format(10),
                                                                PRICING_BODY.format(12))
        self.assertEqual(finalized.outcome, "changed")
        await self.complete_run(self.run_id)
        brief = await self.briefs.build_brief(self.watchlist.id, self.revision.id, self.run_id, self.owner, NOW)
        self.assertEqual((brief.outcome, brief.quality), ("changes_detected", "complete"))
        self.assertEqual(len(brief.findings), len(candidates))
        self.assertTrue(all(finding.verification == "observed" for finding in brief.findings))
        stored = Path(self.tmp.name, brief.artifact_uri)
        self.assertTrue(stored.is_file())
        self.assertNotIn("all findings verified", stored.read_text(encoding="utf-8").lower())
        fetched = await self.briefs.get_brief(str(brief.id), self.owner)
        self.assertEqual(fetched.id, brief.id)
        items, total = await self.briefs.list_briefs(str(self.watchlist.id), self.owner, 50, 0)
        self.assertEqual((len(items), total), (1, 1))
        path, filename = await self.briefs.download_brief(str(brief.id), self.owner)
        self.assertTrue(path.is_file())
        self.assertTrue(filename.endswith(".md"))
        with self.assertRaises(ResourceNotFoundError):
            await self.briefs.get_brief(str(brief.id), str(uuid4()))

    async def test_partial_brief_for_ineligible_capture(self) -> None:
        outcome, snapshot = await self.snapshot_service.capture(**self.capture_kwargs(self.run_id, "too short"))
        self.assertEqual(snapshot.quality, "ineligible")
        comparison, _ = await self.snapshot_service.compare_source(
            self.owner, self.run_id, self.source.id, snapshot.id, NOW)
        self.assertEqual(comparison.outcome, "unavailable")
        await self.snapshot_service.finalize_source(self.owner, comparison, [], "completed", NOW, None)
        await self.complete_run(self.run_id)
        brief = await self.briefs.build_brief(self.watchlist.id, self.revision.id, self.run_id, self.owner, NOW)
        self.assertEqual(brief.outcome, "partial")
        self.assertTrue(brief.limitations)

    async def test_legacy_schema_upgrade_adds_briefs(self) -> None:
        schema = "ci_brief_" + uuid4().hex
        backend = Path(__file__).resolve().parents[1] / "backend"
        def migrate(connection, target: str) -> None:
            config = Config(str(backend.parent / "alembic.ini"))
            config.set_main_option("script_location", str(backend / "alembic"))
            config.attributes["connection"] = connection
            command.upgrade(config, target)
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.run_sync(lambda conn: migrate(conn, "0008_ci_investigation"))
                await connection.run_sync(lambda conn: migrate(conn, "head"))
                tables = (await connection.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=:schema"), {"schema": schema})
                    ).scalars().all()
                self.assertIn("ci_briefs", tables)
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                self.assertEqual(version, "0010_ci_comparison_promotion")
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
