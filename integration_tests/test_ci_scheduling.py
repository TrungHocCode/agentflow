"""CI-P8 real-adapter gates on explicitly disposable PostgreSQL/Redis targets only."""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import MagicMock

from integration_tests.environment import require_integration_environment

require_integration_environment()

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.init_db import init_tables, seed_defaults
from app.db.postgres_client import AsyncSessionLocal, engine
from app.db.redis_client import close_redis_connection
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository
from app.infrastructure.postgres.intelligence_repository import PostgresIntelligenceRepository
from app.infrastructure.postgres.intelligence_schedule_repository import PostgresScheduleRepository
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository
from app.modules.competitive_intelligence.models import CreateWatchlist
from app.modules.competitive_intelligence.scheduling_service import SchedulingService
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.workflows.validator import validate_workflow_references

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestCIRealScheduling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = str(uuid4())
        self.session = AsyncSessionLocal()
        self.addAsyncCleanup(self.cleanup)
        self.repository = PostgresScheduleRepository()
        workflow_repo = PostgresWorkflowRepository(self.session)
        catalog = PostgresCatalogRepository(self.session)
        definition = await validate_workflow_references(normalize_workflow_definition({"tasks": [
            {"id": 1, "task_key": "collect", "node": "source_researcher", "tool_names": ["news_crawler"],
             "description": "Collect", "status": "pending"}]}), catalog)
        workflow = await workflow_repo.create("CI schedule fixture", None, self.owner, definition)
        self.version = workflow.version_id
        intelligence = IntelligenceService(PostgresIntelligenceRepository(), MagicMock())
        self.service = SchedulingService(self.repository, intelligence)
        request = CreateWatchlist.model_validate({"name": "Scheduled competitors", "config": {
            "goal": "Assess pricing changes", "dimensions": ["pricing"],
            "workflow_version_id": self.version,
            "comparison_criteria": [{"field": "price", "objective": "SMB affordability"}],
            "products": [{"name": "Ours", "kind": "own", "official_website": "https://ours.invalid/"},
                         {"name": "Rival", "kind": "competitor", "official_website": "https://rival.invalid/",
                          "sources": [{"url": "https://rival.invalid/pricing", "kind": "pricing"}]}]}})
        self.watchlist = await intelligence.create(request, self.owner)

    async def cleanup(self) -> None:
        await self.session.close()
        await close_redis_connection()
        await engine.dispose()

    async def test_schedule_crud_and_single_winner_claim(self) -> None:
        from app.modules.competitive_intelligence.schedule_contracts import CreateSchedule, UpdateSchedule
        past = NOW - timedelta(hours=2)
        created = await self.repository.create_schedule(
            str(self.watchlist.id), self.owner, "daily", "UTC", "08:00", None, past)
        self.assertLessEqual(created.next_due_at, NOW)
        other = PostgresScheduleRepository()
        first, second = await asyncio.gather(
            self.repository.claim_due(NOW), other.claim_due(NOW))
        mine = [row for row in first if str(row.id) == str(created.id)]
        theirs = [row for row in second if str(row.id) == str(created.id)]
        self.assertEqual(len(mine) + len(theirs), 1)
        advanced = await self.repository.advance(str(created.id), self.owner, NOW)
        self.assertGreater(advanced.next_due_at, NOW)
        updated = await self.service.update_schedule(
            str(created.id), str(self.watchlist.id), self.owner,
            UpdateSchedule(active=False), NOW)
        self.assertFalse(updated.active)
        self.assertEqual(await self.repository.claim_due(NOW + timedelta(days=30)), [])

    async def test_notifications_round_trip_and_isolation(self) -> None:
        stored = await self.repository.notify(self.owner, "schedule_fired", "Run started.",
                                              "Occurrence 20261009.", str(self.watchlist.id))
        items, unread, total = await self.service.list_notifications(self.owner, 50, False)
        self.assertEqual((len(items), unread, total), (1, 1, 1))
        read = await self.service.mark_notification_read(str(stored.id), self.owner, NOW)
        self.assertIsNotNone(read.read_at)
        items, unread, total = await self.service.list_notifications(self.owner, 50, True)
        self.assertEqual((len(items), unread), (0, 0))
        with self.assertRaises(Exception):
            await self.service.mark_notification_read(str(stored.id), str(uuid4()), NOW)

    async def test_legacy_schema_upgrade_adds_scheduling(self) -> None:
        schema = "ci_sched_" + uuid4().hex
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
                await connection.run_sync(lambda conn: migrate(conn, "0010_ci_comparison_promotion"))
                await connection.run_sync(lambda conn: migrate(conn, "head"))
                tables = (await connection.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=:schema"), {"schema": schema})
                    ).scalars().all()
                self.assertIn("ci_schedules", tables)
                self.assertIn("ci_notifications", tables)
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                self.assertEqual(version, "0011_ci_scheduling")
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
