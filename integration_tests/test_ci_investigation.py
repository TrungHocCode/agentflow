"""CI-P4 real-adapter gates on explicitly disposable PostgreSQL/Redis targets only."""

import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, MagicMock

from integration_tests.environment import require_integration_environment

require_integration_environment()

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.db.init_db import init_tables, seed_defaults
from app.db.postgres_client import AsyncSessionLocal, engine
from app.db.redis_client import close_redis_connection, get_redis
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository
from app.infrastructure.postgres.intelligence_repository import PostgresIntelligenceRepository
from app.infrastructure.postgres.intelligence_investigation_repository import PostgresInvestigationRepository
from app.infrastructure.postgres.models import AgentCatalogModel
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository
from app.infrastructure.redis.run_queue import RedisRunCommandQueue
from app.modules.competitive_intelligence.investigation_contracts import (
    InvestigationQuestion, RoundScope,
)
from app.modules.competitive_intelligence.investigation_coordinator import scope_digest
from app.modules.competitive_intelligence.investigation_service import InvestigationService
from app.modules.competitive_intelligence.models import (
    CreateWatchlist, InvestigationBudget, StartRunRequest,
)
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate
from app.modules.competitive_intelligence.snapshot_normalize import hash_text
from app.modules.runs.service import RunService
from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.workflows.validator import validate_workflow_references
from app.shared.errors import ConflictError, ResourceNotFoundError

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestCIRealInvestigation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = str(uuid4())
        self.queue_key = f"agentflow:test:ci-inv:{uuid4()}"
        self.session = AsyncSessionLocal()
        self.addAsyncCleanup(self.cleanup)
        self.repository = PostgresIntelligenceRepository()
        self.rounds = PostgresInvestigationRepository()
        self.snapshots = AsyncMock()
        self.snapshots.get_snapshot = AsyncMock(side_effect=ResourceNotFoundError("gone", entity="snapshot"))
        self.investigation = InvestigationService(self.rounds, self.snapshots)
        workflow_repo = PostgresWorkflowRepository(self.session)
        catalog = PostgresCatalogRepository(self.session)
        definition = await validate_workflow_references(normalize_workflow_definition({"tasks": [
            {"id": 1, "task_key": "collect", "node": "source_researcher", "tool_names": ["news_crawler"],
             "description": "Collect approved public pricing", "status": "pending"}]}), catalog)
        workflow = await workflow_repo.create("CI investigation fixture", None, self.owner, definition)
        self.version = workflow.version_id
        self.runs = RunService(PostgresRunRepository(), workflow_repo, MagicMock(),
            command_queue=RedisRunCommandQueue(self.queue_key), catalog_repository=catalog)
        self.service = IntelligenceService(self.repository, self.runs)
        self.request = CreateWatchlist.model_validate({"name": "Round competitors", "config": {
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
        self.source_id = revision.config.products[1].sources[0].id
        started = await self.service.start(str(self.watchlist.id),
            StartRunRequest(revision_id=revision.id, workflow_version_id=self.version), self.owner, None)
        self.run = started
        self.scope = RoundScope(approved_source_ids=[self.source_id],
                                approved_urls=["https://rival.invalid/pricing"],
                                config_hash=revision.config_hash)
        self.budget = InvestigationBudget()
        self.run_id = UUID(started.run_id)

    async def cleanup(self) -> None:
        await self.session.close()
        await (await get_redis()).delete(self.queue_key)
        await close_redis_connection()
        await engine.dispose()

    def candidate(self) -> ChangeCandidate:
        before, after = uuid4(), uuid4()
        return ChangeCandidate(id=uuid4(), run_id=self.run_id, source_id=self.source_id,
            before_snapshot_id=before, after_snapshot_id=after, kind="modified", section="Pricing",
            before_start=0, before_end=10, after_start=0, after_end=12, before_excerpt="Pro costs $10.",
            after_excerpt="Pro costs $12.", diff_algorithm_version="ci-section-diff-v1",
            diff_hash=hash_text(str(after)), detected_at=NOW)

    def question(self, candidate_ids=()) -> InvestigationQuestion:
        return InvestigationQuestion(id=uuid4(), text="Did Rival pricing change?", dimension="pricing",
                                     candidate_ids=list(candidate_ids))

    async def test_propose_persist_finalize_and_lineage(self) -> None:
        candidate = self.candidate()
        stored, outcome = await self.investigation.propose_round(
            self.run_id, self.watchlist.id, self.revision_id, self.owner, [self.question([candidate.id])],
            [candidate], self.scope, self.budget, 0, 0, 0, False, NOW)
        self.assertEqual((stored.round_number, outcome, stored.status), (1, "accepted", "accepted"))
        self.assertEqual(stored.scope_digest, scope_digest(self.scope))
        listed = await self.rounds.list_rounds(str(self.run_id), self.owner)
        self.assertEqual(len(listed), 1)
        tasks = await self.investigation.materialize_tasks(stored, 10, self.owner)
        self.assertEqual([task.id for task in tasks], list(range(10, 10 + len(tasks))))
        self.assertTrue(all(task.config["investigation_round_id"] == str(stored.id) for task in tasks))
        with self.assertRaises(ValueError):
            await self.investigation.finalize_round(str(stored.id), self.owner, tasks, NOW)
        done = [task.model_copy(update={"status": "done"}) for task in tasks]
        finalized = await self.investigation.finalize_round(str(stored.id), self.owner, done, NOW)
        self.assertEqual(finalized.status, "completed")
        with self.assertRaises(ConflictError):
            await self.rounds.save_round(stored.model_copy(update={"id": uuid4()}), self.owner)

    async def test_rejected_rounds_and_invalid_transitions_fail_visibly(self) -> None:
        accepted, outcome = await self.investigation.propose_round(
            self.run_id, self.watchlist.id, self.revision_id, self.owner, [], [self.candidate()],
            self.scope, self.budget, 0, 0, 0, False, NOW)
        self.assertEqual(outcome, "accepted")
        stored, outcome = await self.investigation.propose_round(
            self.run_id, self.watchlist.id, self.revision_id, self.owner, [], [self.candidate()],
            self.scope, InvestigationBudget(max_tasks=1), 0, 0, 0, False, NOW)
        self.assertEqual((outcome, stored.status), ("rejected", "rejected"))
        self.assertTrue(stored.rejection_reasons)
        with self.assertRaises(ConflictError):
            await self.rounds.set_round_status(str(stored.id), self.owner, "completed", "test")
        with self.assertRaises(ResourceNotFoundError):
            await self.rounds.get_round(str(accepted.id), str(uuid4()))
        renamed = await self.rounds.set_round_status(str(accepted.id), self.owner, "superseded", "test")
        self.assertEqual(renamed.status, "superseded")

    async def test_accepted_rounds_are_immutable(self) -> None:
        stored, _ = await self.investigation.propose_round(
            self.run_id, self.watchlist.id, self.revision_id, self.owner, [], [self.candidate()],
            self.scope, self.budget, 0, 0, 0, False, NOW)
        session = AsyncSessionLocal()
        try:
            with self.assertRaises(Exception):
                async with session.begin():
                    await session.execute(text("UPDATE ci_investigation_rounds SET tasks='[]' WHERE id=:id"),
                                          {"id": str(stored.id)})
        finally:
            await session.close()

    async def test_ci_roles_are_seeded_and_scoped(self) -> None:
        async with AsyncSessionLocal() as session:
            names = set((await session.execute(select(AgentCatalogModel.name))).scalars().all())
        for role in ("product_analyst", "evidence_verifier", "competitive_analyst"):
            self.assertIn(role, names)

    async def test_legacy_schema_upgrade_adds_rounds(self) -> None:
        schema = "ci_inv_" + uuid4().hex
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
                await connection.run_sync(lambda conn: migrate(conn, "0007_ci_snapshots"))
                await connection.run_sync(lambda conn: migrate(conn, "head"))
                tables = (await connection.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=:schema"), {"schema": schema})
                    ).scalars().all()
                self.assertIn("ci_investigation_rounds", tables)
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                self.assertEqual(version, "0010_ci_comparison_promotion")
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
