"""CI live-loop gates on explicitly disposable PostgreSQL/Redis targets only (no model)."""

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import MagicMock

from integration_tests.environment import require_integration_environment

require_integration_environment()

from sqlalchemy import text

from app.db.init_db import init_tables, seed_defaults
from app.db.postgres_client import AsyncSessionLocal, engine
from app.db.redis_client import close_redis_connection, get_redis
from app.execution.state import Task
from app.infrastructure.artifacts.brief_blobs import BriefFileStore
from app.infrastructure.artifacts.snapshot_blobs import SnapshotFileStore
from app.infrastructure.artifacts.storage import LocalArtifactStorage
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository
from app.infrastructure.postgres.intelligence_brief_repository import PostgresBriefRepository
from app.infrastructure.postgres.intelligence_investigation_repository import PostgresInvestigationRepository
from app.infrastructure.postgres.intelligence_repository import PostgresIntelligenceRepository
from app.infrastructure.postgres.intelligence_snapshot_repository import PostgresSnapshotRepository
from app.infrastructure.postgres.results_repository import PostgresResearchRepository
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository
from app.infrastructure.redis.run_queue import RedisRunCommandQueue
from app.modules.competitive_intelligence.brief_service import BriefService
from app.modules.competitive_intelligence.investigation_service import InvestigationService
from app.modules.competitive_intelligence.loop_hooks import CILoopHooks
from app.modules.competitive_intelligence.models import CreateWatchlist, StartRunRequest
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.snapshot_service import SnapshotService
from app.modules.results.models import ResultRecord
from app.modules.runs.service import RunService
from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.workflows.validator import validate_workflow_references

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
PRICING_BODY = "# Pricing\n\nPro plan costs ${} per month, billed annually ex VAT.\n\n## Features\n\nSSO included.\n"
PRICING_V1 = PRICING_BODY.format(10) * 4
PRICING_V2 = PRICING_BODY.format(12) * 4


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestCIRealLoop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = str(uuid4())
        self.queue_key = f"agentflow:test:ci-loop:{uuid4()}"
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
             "description": "Collect approved public pricing", "status": "pending"}]}), catalog)
        workflow = await workflow_repo.create("CI loop fixture", None, self.owner, definition)
        self.version = workflow.version_id
        self.runs = RunService(PostgresRunRepository(), workflow_repo, MagicMock(),
            command_queue=RedisRunCommandQueue(self.queue_key), catalog_repository=catalog)
        self.service = IntelligenceService(self.repository, self.runs)
        briefs = BriefService(PostgresBriefRepository(), snapshots, BriefFileStore(root=self.tmp.name),
                              self.runs)
        self.hooks = CILoopHooks(snapshots, investigations, briefs,
                                 PostgresResearchRepository(self.session),
                                 LocalArtifactStorage(root=self.tmp.name))
        self.request = CreateWatchlist.model_validate({"name": "Loop competitors", "config": {
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
        self.source_url = "https://rival.invalid/pricing"

    async def cleanup(self) -> None:
        await self.session.close()
        await (await get_redis()).delete(self.queue_key)
        await close_redis_connection()
        await engine.dispose()

    async def close_tmp(self) -> None:
        self.tmp.cleanup()

    async def start_run(self):
        started = await self.service.start(str(self.watchlist.id),
            StartRunRequest(revision_id=self.revision.id, workflow_version_id=self.version), self.owner, None)
        return await self.runs.get_run(started.run_id, self.owner)

    async def store_document(self, run_id: str, task_id: int, text: str) -> None:
        uri = f"test-docs/{run_id}/{task_id}.txt"
        target = Path(self.tmp.name, uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        await PostgresResearchRepository(self.session).save_result(ResultRecord(
            id=str(uuid4()), run_id=run_id, task_id=str(task_id), result_type="raw_data",
            content={"source_url": self.source_url, "storage_uri": uri},
            metadata={"kind": "source_document"}))

    async def complete_run(self, run_id: str):
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(text("UPDATE runs SET status='completed' WHERE run_id=:id"),
                                      {"id": run_id})
        return await self.runs.get_run(run_id, self.owner)

    def researcher_task(self, task_id: int = 1) -> Task:
        return Task(id=task_id, node="source_researcher", agent_id="source_researcher",
                    tool_names=["news_crawler"], description="Collect", status="done")

    async def test_two_run_loop_from_baseline_to_brief(self) -> None:
        run1 = await self.start_run()
        self.assertTrue(all(value is None for value in
                            run1.input_data["competitive_intelligence"]["baselines"].values()))
        await self.store_document(run1.run_id, 1, PRICING_V1)
        tasks = await self.hooks.on_task_completed(run1, self.researcher_task(), "done")
        self.assertEqual(tasks, [])
        run1 = await self.complete_run(run1.run_id)
        await self.hooks.on_run_completed(run1)
        briefs1 = await self.hooks.briefs.list_briefs(str(self.watchlist.id), self.owner, 50, 0)
        self.assertEqual(briefs1[1], 1)
        self.assertEqual(briefs1[0][0].outcome, "baseline_created")

        run2 = await self.start_run()
        pinned = run2.input_data["competitive_intelligence"]["baselines"]
        self.assertTrue(all(value is not None for value in pinned.values()))
        await self.store_document(run2.run_id, 1, PRICING_V2)
        tasks = await self.hooks.on_task_completed(run2, self.researcher_task(), "done")
        self.assertTrue(tasks)
        roles = {task.agent_id for task in tasks}
        self.assertTrue({"source_researcher", "evidence_verifier"} <= roles)
        self.assertTrue(all(task.config.get("investigation_round_id") for task in tasks))
        run2 = await self.complete_run(run2.run_id)
        await self.hooks.on_run_completed(run2)
        briefs2 = await self.hooks.briefs.list_briefs(str(self.watchlist.id), self.owner, 50, 0)
        self.assertEqual(briefs2[1], 2)
        latest = await self.hooks.snapshots.latest_baseline(
            str(self.revision.config.products[1].sources[0].id), self.owner)
        self.assertIsNotNone(latest)
