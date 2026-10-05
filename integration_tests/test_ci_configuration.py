"""CI real-adapter gates on explicitly disposable PostgreSQL/Redis targets only."""

import asyncio
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import MagicMock

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
from app.infrastructure.postgres.models import ProductProfileVersionModel, RunModel, WatchlistRevisionModel
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository
from app.infrastructure.redis.run_queue import RedisRunCommandQueue
from app.modules.competitive_intelligence.models import CreateWatchlist, StartRunRequest, UpdateWatchlist
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.runs.service import RunService
from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.workflows.validator import validate_workflow_references
from app.shared.errors import ConflictError, PersistenceError, ResourceNotFoundError, ValidationError


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestCIRealConfiguration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = str(uuid4())
        self.queue_key = f"agentflow:test:ci:{uuid4()}"
        self.session = AsyncSessionLocal()
        self.addAsyncCleanup(self.cleanup)
        self.repository = PostgresIntelligenceRepository()
        workflow_repo = PostgresWorkflowRepository(self.session)
        catalog = PostgresCatalogRepository(self.session)
        definition = await validate_workflow_references(normalize_workflow_definition({"tasks": [
            {"id": 1, "task_key": "collect", "node": "source_researcher", "tool_names": ["news_crawler"],
             "description": "Collect approved public pricing", "status": "pending"}]}), catalog)
        workflow = await workflow_repo.create("CI fixture", None, self.owner, definition)
        self.version = workflow.version_id
        self.runs = RunService(PostgresRunRepository(), workflow_repo, MagicMock(),
            command_queue=RedisRunCommandQueue(self.queue_key), catalog_repository=catalog)
        self.service = IntelligenceService(self.repository, self.runs)
        self.request = CreateWatchlist.model_validate({"name": "Private competitors", "config": {
            "goal": "Assess pricing changes for small teams", "dimensions": ["pricing"],
            "workflow_version_id": self.version, "comparison_criteria": [
                {"field": "price", "objective": "SMB affordability"}],
            "internal_strategy": "private launch strategy",
            "products": [{"name": "Ours", "kind": "own", "official_website": "https://ours.invalid/",
                "profile": {"facts": [{"field": "price", "value": "$10/month"}], "target_segments": ["SMB"]}},
                {"name": "Rival", "kind": "competitor", "official_website": "https://rival.invalid/",
                 "sources": [{"url": "https://rival.invalid/pricing", "kind": "pricing"}]}]}})
        self.watchlist = await self.service.create(self.request, self.owner)

    async def cleanup(self) -> None:
        # Immutable CI history stays until the disposable test database is discarded.
        await self.session.close()
        await (await get_redis()).delete(self.queue_key)
        await close_redis_connection()
        await engine.dispose()

    def update_request(self, watchlist=None) -> UpdateWatchlist:
        watchlist = watchlist or self.watchlist
        return UpdateWatchlist(name=watchlist.name, description="Edited",
            config=watchlist.current_revision.config.model_copy(deep=True),
            expected_revision_id=watchlist.current_revision.id)

    async def approved(self):
        revision = await self.service.approve(str(self.watchlist.id), str(self.watchlist.current_revision.id),
                                             self.version, self.owner)
        return StartRunRequest(revision_id=revision.id, workflow_version_id=self.version)

    async def test_revision_profile_history_stale_edits_approval_and_retained_archive(self) -> None:
        await self.approved()
        update = self.update_request()
        update.config.products[0].profile.facts[0].value = "$12/month"
        update.config.products[0].profile_version_id = None  # Explicit new profile version.
        changed = await self.service.update(str(self.watchlist.id), update, self.owner)
        self.assertEqual(changed.current_revision.approval_status, "unapproved")
        self.assertNotEqual(changed.current_revision.config.products[0].profile_version_id,
                            self.watchlist.current_revision.config.products[0].profile_version_id)
        old = (await self.repository.revisions(str(self.watchlist.id), self.owner))[0]
        self.assertEqual(old.config.products[0].profile.facts[0].value, "$10/month")
        self.assertEqual(old.approval_status, "approved")
        with self.assertRaises(ConflictError):
            await self.service.update(str(self.watchlist.id), update, self.owner)
        with self.assertRaises(ConflictError):
            await self.service.approve(str(self.watchlist.id), str(old.id), self.version, self.owner)
        async with AsyncSessionLocal() as session:
            row = await session.get(WatchlistRevisionModel, str(old.id))
            row.config_hash = "0" * 64
            with self.assertRaises(Exception):
                await session.commit()
            await session.rollback()
            profile = await session.get(ProductProfileVersionModel, str(old.config.products[0].profile_version_id))
            profile.profile_hash = "0" * 64
            with self.assertRaises(Exception):
                await session.commit()
            await session.rollback()
        await self.repository.archive(str(changed.id), self.owner)
        self.assertEqual((await self.repository.get(str(changed.id), self.owner)).status, "archived")
        self.assertEqual(len(await self.repository.revisions(str(changed.id), self.owner)), 2)

    async def test_linked_membership_profiles_evidence_and_reads_are_owner_scoped(self) -> None:
        other = await self.service.create(self.request, str(uuid4()))
        for operation in (self.repository.get, self.repository.revisions):
            with self.assertRaises(ResourceNotFoundError):
                await operation(str(self.watchlist.id), other.owner_id)
        update = self.update_request()
        update.config.products[1].id = other.current_revision.config.products[1].id
        with self.assertRaises(ResourceNotFoundError):
            await self.service.update(str(self.watchlist.id), update, self.owner)
        update = self.update_request()
        update.config.products[1].sources[0].id = other.current_revision.config.products[1].sources[0].id
        with self.assertRaises(ResourceNotFoundError):
            await self.service.update(str(self.watchlist.id), update, self.owner)
        update = self.update_request()
        update.config.products[0].profile_version_id = other.current_revision.config.products[0].profile_version_id
        with self.assertRaises(ValidationError):
            await self.service.update(str(self.watchlist.id), update, self.owner)
        update = self.update_request()
        fact = update.config.products[0].profile.facts[0]
        fact.provenance, fact.evidence_ids = "evidence_linked", [uuid4()]
        with self.assertRaises(ResourceNotFoundError):
            await self.service.update(str(self.watchlist.id), update, self.owner)

    async def test_run_admission_freezes_input_and_is_idempotent_with_cancel_archive(self) -> None:
        request = StartRunRequest(revision_id=self.watchlist.current_revision.id, workflow_version_id=self.version)
        with self.assertRaises(ConflictError):
            await self.service.start(str(self.watchlist.id), request, self.owner, "one")
        await self.approved()
        run = await self.service.start(str(self.watchlist.id), request, self.owner, "one")
        self.assertEqual(run.status, "queued")
        self.assertEqual(run.watchlist_id, str(self.watchlist.id))
        self.assertEqual((await self.repository.runs(str(self.watchlist.id), self.owner, 50, 0))[0].run_id, run.run_id)
        duplicate = await self.service.start(str(self.watchlist.id), request, self.owner, "one")
        self.assertEqual(run.run_id, duplicate.run_id)
        self.assertEqual(await (await get_redis()).llen(self.queue_key), 1)
        with self.assertRaises(ConflictError):
            await self.service.start(str(self.watchlist.id), request, self.owner, "two")
        with self.assertRaises(ConflictError):
            await self.repository.archive(str(self.watchlist.id), self.owner)
        update = self.update_request()
        update.config.goal = "New objective"
        changed = await self.service.update(str(self.watchlist.id), update, self.owner)
        persisted = await self.runs.get_run(run.run_id, self.owner)
        self.assertEqual(persisted.input_data["competitive_intelligence"]["config"]["goal"], self.request.config.goal)
        duplicate = await self.service.start(str(self.watchlist.id), request, self.owner, "one")
        self.assertEqual(duplicate.run_id, run.run_id)
        await self.runs.cancel_run(run.run_id, self.owner)
        await self.repository.archive(str(changed.id), self.owner)
        self.assertEqual(len(await self.repository.runs(str(changed.id), self.owner, 50, 0)), 1)

    async def test_transactional_parallel_admission_permits_only_one_active_run(self) -> None:
        request = await self.approved()
        watchlist = await self.repository.get(str(self.watchlist.id), self.owner)
        first = await self.service.prepare(watchlist, str(request.revision_id), self.version)
        second = await self.service.prepare(watchlist, str(request.revision_id), self.version)
        results = await asyncio.gather(*(self.repository.admit_run(doc, str(watchlist.id), str(request.revision_id))
                                       for doc in (first, second)), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, ConflictError) for value in results), 1)
        self.assertEqual(len(await self.repository.runs(str(watchlist.id), self.owner, 50, 0)), 1)

    async def test_accepted_run_scope_is_immutable_and_worker_uses_tightened_budget(self) -> None:
        update = self.update_request()
        update.config.budget.max_llm_calls = 1
        self.watchlist = await self.service.update(str(self.watchlist.id), update, self.owner)
        request = await self.approved()
        run = await self.service.start(str(self.watchlist.id), request, self.owner, None)
        async with AsyncSessionLocal() as session:
            row = await session.get(RunModel, run.run_id)
            row.input_data = {"competitive_intelligence": {"approved_urls": ["https://intruder.invalid/"]}}
            with self.assertRaises(Exception):
                await session.commit()
            await session.rollback()
        from langchain_core.messages import HumanMessage
        from app.execution.run_budget import bounded_invoke
        from app.execution.tools.network_policy import validate_external_url
        from unittest.mock import AsyncMock
        model = AsyncMock()
        class ProbeExecution:
            async def execute_run(self, run_id: str, state: dict):
                self.blocked = validate_external_url("https://intruder.invalid/")[1]
                await bounded_invoke(model, [HumanMessage(content="first")])
                await bounded_invoke(model, [HumanMessage(content="second")])
                yield {}
        probe = ProbeExecution()
        self.runs.execution_port = probe
        failed = await self.runs.execute_queued_run(run.run_id)
        self.assertEqual(failed.error_code, "run_budget_exhausted")
        self.assertEqual(model.ainvoke.await_count, 1)
        self.assertIsNotNone(probe.blocked)

    async def test_legacy_schema_upgrade_preserves_old_runs(self) -> None:
        schema = "ci_upgrade_" + uuid4().hex
        backend = Path(__file__).resolve().parents[1] / "backend"
        def migrate(connection, target: str) -> None:
            config = Config(str(backend / "alembic.ini"))
            config.set_main_option("script_location", str(backend / "alembic"))
            config.attributes["connection"] = connection
            command.upgrade(config, target)
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.run_sync(lambda conn: migrate(conn, "0005_durable_conversation_turns"))
                identity = str(uuid4())
                await connection.execute(text("INSERT INTO flows(id,name,user_id,definition,status) "
                    "VALUES (:id,'old','legacy','{}','active')"), {"id": identity})
                # Reuse the pre-CI ORM values without the two newly added columns.
                from app.modules.runs.models import RunDocument
                values = PostgresRunRepository._to_orm_values(RunDocument(
                    run_id=identity, flow_id=identity, user_id="legacy", status="completed"))
                values.pop("watchlist_id")
                values.pop("watchlist_revision_id")
                legacy_table = RunModel.__table__.to_metadata(__import__("sqlalchemy").MetaData())
                await connection.execute(legacy_table.insert().values(**values))
                await connection.run_sync(lambda conn: migrate(conn, "head"))
                row = (await connection.execute(text("SELECT status,watchlist_id,watchlist_revision_id FROM runs "
                                                      "WHERE run_id=:id"), {"id": identity})).one()
                self.assertEqual(tuple(row), ("completed", None, None))
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
