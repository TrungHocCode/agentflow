"""Small real-adapter baseline; not a claim of full workflow E2E coverage."""

import asyncio
import os
import unittest
from uuid import uuid4

from integration_tests.environment import require_integration_environment

require_integration_environment()

import httpx  # noqa: E402
from sqlalchemy import delete, inspect, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.init_db import init_tables, seed_defaults  # noqa: E402
from app.db.postgres_client import AsyncSessionLocal, engine  # noqa: E402
from app.db.redis_client import close_redis_connection, get_redis  # noqa: E402
from app.infrastructure.container import build_run_queue  # noqa: E402
from app.infrastructure.postgres.models import FlowModel, RunModel  # noqa: E402
from app.infrastructure.postgres.run_repository import PostgresRunRepository  # noqa: E402
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository  # noqa: E402
from app.infrastructure.redis.run_queue import RedisRunCommandQueue  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.runs.models import RunDocument  # noqa: E402
from app.shared.commands import RunCommand  # noqa: E402
from app.shared.events import ExecutionEvent  # noqa: E402


def setUpModule() -> None:
    require_integration_environment()
    if settings.POSTGRES_URL != os.environ["POSTGRES_URL"] or settings.REDIS_URL != os.environ["REDIS_URL"]:
        raise RuntimeError("Settings do not match isolated integration targets.")
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestRealPersistence(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        require_integration_environment()
        self.repository = PostgresRunRepository()
        self.assertFalse(self.repository.use_memory, "Integration must not use memory fallback")
        self.assertIsInstance(build_run_queue(), RedisRunCommandQueue)
        self.owner = str(uuid4())
        self.flow_id = str(uuid4())
        self.run_id = str(uuid4())
        self.queue_key = f"agentflow:test:{uuid4()}:commands"
        self.addAsyncCleanup(self.cleanup_records)
        async with AsyncSessionLocal() as session:
            session.add(FlowModel(id=self.flow_id, name="Integration fixture", user_id=self.owner, definition={}))
            await session.commit()
        await self.repository.save(RunDocument(
            run_id=self.run_id, flow_id=self.flow_id, user_id=self.owner, status="queued",
        ))

    async def cleanup_records(self) -> None:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(delete(RunModel).where(RunModel.run_id == self.run_id))
                await session.execute(delete(FlowModel).where(FlowModel.user_id == self.owner))
                await session.commit()
            await (await get_redis()).delete(self.queue_key)
        finally:
            await close_redis_connection()
            await engine.dispose()

    async def test_migrations_are_repeatable_and_create_required_tables(self) -> None:
        await init_tables()
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            self.assertTrue({"flows", "workflow_versions", "runs", "run_events", "results", "users"} <= set(tables))
            self.assertEqual(await connection.scalar(text("SELECT version_num FROM alembic_version")),
                             "0003_research_outputs")

    async def test_concurrent_claim_has_one_winner_and_owner_filter_is_enforced(self) -> None:
        outcomes = await asyncio.gather(*(self.repository.claim(self.run_id) for _ in range(4)))
        self.assertEqual(sum(item is not None for item in outcomes), 1)
        self.assertEqual((await self.repository.get(self.run_id, self.owner)).status, "running")
        self.assertIsNone(await self.repository.get(self.run_id, "another-owner"))

    async def test_events_persist_and_replay_after_cursor(self) -> None:
        first = await self.repository.append_event(ExecutionEvent(run_id=self.run_id, type="run_started"))
        second = await self.repository.append_event(ExecutionEvent(run_id=self.run_id, type="run_completed"))
        recovered = await PostgresRunRepository().list_events(self.run_id, after_event_id=first.event_id)
        self.assertEqual([item.event_id for item in recovered], [second.event_id])
        self.assertEqual(second.sequence, first.sequence + 1)

    async def test_queue_round_trip_uses_real_redis(self) -> None:
        queue = RedisRunCommandQueue(key=self.queue_key)
        command = RunCommand(command_id=str(uuid4()), run_id=self.run_id, workflow_id=self.flow_id)
        await queue.enqueue(command)
        self.assertEqual(await queue.dequeue(timeout=1), command)
        self.assertIsNone(await queue.dequeue(timeout=1))

    async def test_workflow_version_is_stored_and_read_by_another_session(self) -> None:
        async with AsyncSessionLocal() as session:
            repo = PostgresWorkflowRepository(session)
            self.assertFalse(repo.use_memory)
            workflow = await repo.create("Stored workflow", None, self.owner, {"tasks": []})
        async with engine.connect() as connection:
            version = await connection.scalar(text(
                "SELECT id FROM workflow_versions WHERE workflow_id = :workflow_id"
            ), {"workflow_id": workflow.id})
            self.assertEqual(version, workflow.version_id)

    async def test_protected_api_rejects_unauthenticated_requests_without_test_bypass(self) -> None:
        self.assertEqual(app.dependency_overrides, {})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/runs")
        self.assertEqual(response.status_code, 401)
