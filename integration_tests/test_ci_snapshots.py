"""CI-P3 real-adapter gates on explicitly disposable PostgreSQL/Redis targets only."""

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
from app.infrastructure.artifacts.snapshot_blobs import SnapshotFileStore
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository
from app.infrastructure.postgres.intelligence_repository import PostgresIntelligenceRepository
from app.infrastructure.postgres.intelligence_snapshot_repository import PostgresSnapshotRepository
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository
from app.infrastructure.redis.run_queue import RedisRunCommandQueue
from app.modules.competitive_intelligence.models import CreateWatchlist, digest
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.snapshot_service import SnapshotService
from app.modules.runs.service import RunService
from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.workflows.validator import validate_workflow_references
from app.shared.errors import ResourceNotFoundError

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
PRICING_BODY = "# Pricing\n\nPro plan costs ${} per month, billed annually ex VAT.\n\n## Features\n\nSSO included.\n"
PRICING_V1 = PRICING_BODY.format(10) * 4
PRICING_V2 = PRICING_BODY.format(12) * 4


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestCIRealSnapshots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = str(uuid4())
        self.queue_key = f"agentflow:test:ci-snap:{uuid4()}"
        self.session = AsyncSessionLocal()
        self.addAsyncCleanup(self.cleanup)
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self.close_tmp)
        self.repository = PostgresIntelligenceRepository()
        self.snapshots = PostgresSnapshotRepository(blobs=SnapshotFileStore(root=self.tmp.name))
        self.snapshot_service = SnapshotService(self.snapshots)
        workflow_repo = PostgresWorkflowRepository(self.session)
        catalog = PostgresCatalogRepository(self.session)
        definition = await validate_workflow_references(normalize_workflow_definition({"tasks": [
            {"id": 1, "task_key": "collect", "node": "source_researcher", "tool_names": ["news_crawler"],
             "description": "Collect approved public pricing", "status": "pending"}]}), catalog)
        workflow = await workflow_repo.create("CI snapshot fixture", None, self.owner, definition)
        self.version = workflow.version_id
        self.runs = RunService(PostgresRunRepository(), workflow_repo, MagicMock(),
            command_queue=RedisRunCommandQueue(self.queue_key), catalog_repository=catalog)
        self.service = IntelligenceService(self.repository, self.runs)
        self.request = CreateWatchlist.model_validate({"name": "Snapshot competitors", "config": {
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
        self.revision_id = revision.id
        self.source = revision.config.products[1].sources[0]
        self.config_version = digest(self.source)

    async def cleanup(self) -> None:
        await self.session.close()
        await (await get_redis()).delete(self.queue_key)
        await close_redis_connection()
        await engine.dispose()

    async def close_tmp(self) -> None:
        self.tmp.cleanup()

    def capture_kwargs(self, **overrides):
        values = {"run_id": uuid4(), "source_id": self.source.id, "revision_id": self.revision_id,
                  "owner_id": self.owner, "requested_url": self.source.url, "final_url": self.source.url,
                  "http_status": 200, "fetch_status": "success", "reason_codes": [],
                  "captured_text": PRICING_V1, "source_kind": "pricing", "language": "en", "region": "US",
                  "source_config_version": self.config_version, "title": "Pricing", "content_type": "text/plain",
                  "observed_at": NOW, "fetched_at": NOW}
        values.update(overrides)
        return values

    async def test_capture_compare_promote_pin_and_stale_cas(self) -> None:
        outcome, first = await self.snapshot_service.capture(**self.capture_kwargs())
        self.assertEqual(first.quality, "eligible")
        self.assertTrue(first.captured_uri.endswith("-captured.txt"))
        comparison, candidates = await self.snapshot_service.compare_source(
            self.owner, outcome.run_id, self.source.id, first.id, NOW)
        self.assertEqual((comparison.outcome, candidates), ("baseline_created", []))
        finalized = await self.snapshot_service.finalize_source(
            self.owner, comparison, candidates, "completed", NOW, None)
        self.assertEqual(finalized.promotion, "promoted")
        pinned = await self.repository.pin_baselines(str(self.watchlist.id), self.owner)
        self.assertEqual(pinned, {str(self.source.id): str(first.id)})

        second_run = uuid4()
        _, second = await self.snapshot_service.capture(
            **self.capture_kwargs(run_id=second_run, captured_text=PRICING_V2))
        comparison, candidates = await self.snapshot_service.compare_source(
            self.owner, second_run, self.source.id, second.id, NOW)
        self.assertEqual(comparison.outcome, "changed")
        self.assertEqual(len(candidates), 1)
        self.assertIn("$12", candidates[0].after_excerpt)
        finalized = await self.snapshot_service.finalize_source(
            self.owner, comparison, candidates, "completed", NOW, first.id)
        self.assertEqual(finalized.promotion, "promoted")
        latest = await self.snapshots.latest_baseline(str(self.source.id), self.owner)
        self.assertEqual(str(latest.id), str(second.id))

        # A late decision against the superseded pointer is rejected, never overwrites.
        stale = await self.snapshot_service.finalize_source(
            self.owner, comparison, candidates, "completed", NOW, first.id)
        self.assertEqual(stale.promotion, "rejected_stale")
        latest = await self.snapshots.latest_baseline(str(self.source.id), self.owner)
        self.assertEqual(str(latest.id), str(second.id))

    async def test_failed_run_and_failed_fetch_never_promote(self) -> None:
        outcome, first = await self.snapshot_service.capture(**self.capture_kwargs())
        comparison, _ = await self.snapshot_service.compare_source(
            self.owner, outcome.run_id, self.source.id, first.id, NOW)
        halted = await self.snapshot_service.finalize_source(
            self.owner, comparison, [], "failed", NOW, None)
        self.assertEqual(halted.promotion, "skipped")
        pinned = await self.repository.pin_baselines(str(self.watchlist.id), self.owner)
        self.assertEqual(pinned, {str(self.source.id): None})
        failed, snapshot = await self.snapshot_service.capture(
            **self.capture_kwargs(fetch_status="failed", http_status=None, captured_text=None,
                                  reason_codes=["timeout"]))
        self.assertIsNone(snapshot)
        self.assertEqual(failed.fetch_status, "failed")

    async def test_cross_owner_snapshot_access_denied(self) -> None:
        _, first = await self.snapshot_service.capture(**self.capture_kwargs())
        with self.assertRaises(ResourceNotFoundError):
            await self.snapshots.get_snapshot(str(first.id), str(uuid4()))
        with self.assertRaises(ResourceNotFoundError):
            await self.snapshots.read_snapshot_span(str(first.id), str(uuid4()), "normalized", 0, 100)
        span = await self.snapshots.read_snapshot_span(str(first.id), self.owner, "normalized", 0, 9)
        self.assertEqual(span, "# Pricing")

    async def test_legacy_schema_upgrade_preserves_ci_configuration(self) -> None:
        schema = "ci_snap_" + uuid4().hex
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
                await connection.run_sync(lambda conn: migrate(conn, "0006_ci_configuration"))
                watchlist_id, revision_id, product_id, source_id = (str(uuid4()) for _ in range(4))
                await connection.execute(text(
                    "INSERT INTO ci_watchlists(id,owner_id,name,status,current_revision_id) "
                    "VALUES (:id,:owner,'legacy','active',:revision)"),
                    {"id": watchlist_id, "owner": self.owner, "revision": revision_id})
                await connection.execute(text(
                    "INSERT INTO ci_watchlist_revisions(id,watchlist_id,revision_number,config,config_hash,"
                    "approval_status) VALUES (:id,:watchlist,1,'{}','hash','approved')"),
                    {"id": revision_id, "watchlist": watchlist_id})
                await connection.execute(text(
                    "INSERT INTO ci_products(id,watchlist_id,owner_id,kind) "
                    "VALUES (:id,:watchlist,:owner,'competitor')"),
                    {"id": product_id, "watchlist": watchlist_id, "owner": self.owner})
                await connection.execute(text(
                    "INSERT INTO ci_sources(id,product_id,watchlist_id,owner_id,config_version) "
                    "VALUES (:id,:product,:watchlist,:owner,'version')"),
                    {"id": source_id, "product": product_id, "watchlist": watchlist_id, "owner": self.owner})
                await connection.run_sync(lambda conn: migrate(conn, "head"))
                tables = (await connection.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=:schema AND tablename LIKE 'ci\\_%'"),
                    {"schema": schema})).scalars().all()
                for table in ("ci_snapshots", "ci_fetch_outcomes", "ci_baselines", "ci_run_comparisons",
                              "ci_change_candidates"):
                    self.assertIn(table, tables)
                row = (await connection.execute(text(
                    "SELECT status,current_revision_id FROM ci_watchlists WHERE id=:id"), {"id": watchlist_id})).one()
                self.assertEqual(tuple(row), ("active", revision_id))
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                self.assertEqual(version, "0007_ci_snapshots")
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
