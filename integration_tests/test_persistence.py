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
from app.execution.state import Task  # noqa: E402
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
                await session.execute(delete(RunModel).where(RunModel.user_id == self.owner))
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
            self.assertTrue(
                {
                    "flows", "workflow_versions", "workflow_steps",
                    "workflow_step_dependencies", "workflow_step_tools",
                    "task_executions", "runs", "run_events", "results", "users",
                }
                <= set(tables)
            )
            self.assertEqual(await connection.scalar(text("SELECT version_num FROM alembic_version")),
                             "0004_workflow_contracts")

    async def test_concurrent_claim_has_one_winner_and_owner_filter_is_enforced(self) -> None:
        outcomes = await asyncio.gather(*(self.repository.claim(self.run_id) for _ in range(4)))
        self.assertEqual(sum(item is not None for item in outcomes), 1)
        self.assertEqual((await self.repository.get(self.run_id, self.owner)).status, "running")
        self.assertIsNone(await self.repository.get(self.run_id, "another-owner"))

    async def test_idempotency_key_is_unique_per_owner_on_postgres(self) -> None:
        other_owner = str(uuid4())
        other_flow_id = str(uuid4())
        first_run_id = str(uuid4())
        second_run_id = str(uuid4())
        async with AsyncSessionLocal() as session:
            session.add(
                FlowModel(
                    id=other_flow_id,
                    name="Second integration fixture",
                    user_id=other_owner,
                    definition={},
                )
            )
            await session.commit()

        try:
            await self.repository.save(
                RunDocument(
                    run_id=first_run_id,
                    flow_id=self.flow_id,
                    user_id=self.owner,
                    idempotency_key="shared-key",
                )
            )
            await self.repository.save(
                RunDocument(
                    run_id=second_run_id,
                    flow_id=other_flow_id,
                    user_id=other_owner,
                    idempotency_key="shared-key",
                )
            )

            first = await self.repository.find_by_idempotency_key("shared-key", self.owner)
            second = await self.repository.find_by_idempotency_key("shared-key", other_owner)
            self.assertEqual(first.run_id, first_run_id)
            self.assertEqual(second.run_id, second_run_id)
        finally:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    delete(RunModel).where(RunModel.run_id.in_([first_run_id, second_run_id]))
                )
                await session.execute(delete(FlowModel).where(FlowModel.id == other_flow_id))
                await session.commit()

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
            workflow = await repo.create(
                "Stored workflow",
                None,
                self.owner,
                {
                    "steps": [
                        {
                            "task_key": "collect",
                            "name": "Collect",
                            "description": "Collect source material",
                            "agent_id": "source_researcher",
                            "dependencies": [],
                            "tool_names": ["web_search"],
                            "expected_output_type": "raw_data",
                            "config": {"timeout_seconds": 90},
                            "position": 0,
                        },
                        {
                            "task_key": "report",
                            "name": "Report",
                            "description": "Write an evidence-grounded report",
                            "agent_id": "report_agent",
                            "dependencies": ["collect"],
                            "expected_output_type": "report",
                            "position": 1,
                        },
                    ]
                },
            )
        async with engine.connect() as connection:
            version = await connection.scalar(text(
                "SELECT id FROM workflow_versions WHERE workflow_id = :workflow_id"
            ), {"workflow_id": workflow.id})
            self.assertEqual(version, workflow.version_id)
            step_count = await connection.scalar(
                text("SELECT count(*) FROM workflow_steps WHERE workflow_version_id = :version_id"),
                {"version_id": workflow.version_id},
            )
            dependency_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM workflow_step_dependencies d "
                    "JOIN workflow_steps s ON s.id = d.step_id "
                    "WHERE s.workflow_version_id = :version_id"
                ),
                {"version_id": workflow.version_id},
            )
            self.assertEqual(step_count, 2)
            self.assertEqual(dependency_count, 1)
        async with AsyncSessionLocal() as session:
            repo = PostgresWorkflowRepository(session)
            snapshot = await repo.get_published_version_snapshot(
                workflow.id,
                workflow.version_id,
                self.owner,
            )
            hidden_snapshot = await repo.get_published_version_snapshot(
                workflow.id,
                workflow.version_id,
                "different-owner",
            )
        self.assertIsNotNone(snapshot)
        self.assertIsNone(hidden_snapshot)

        task_execution_id = str(uuid4())
        task_run_id = str(uuid4())
        run_document = RunDocument(
            run_id=task_run_id,
            flow_id=workflow.id,
            user_id=self.owner,
            workflow_version_id=workflow.version_id,
            status="queued",
            plan=[
                Task(
                    id=1,
                    task_key="collect",
                    task_execution_id=task_execution_id,
                    node="source_researcher",
                    status="pending",
                    description="Collect source material",
                )
            ],
        )
        await self.repository.save(
            run_document
        )
        run_document.plan[0].status = "running"
        await self.repository.save(run_document)
        run_document.plan[0].status = "done"
        await self.repository.save(run_document)
        async with engine.connect() as connection:
            persisted_id = await connection.scalar(
                text(
                    "SELECT id FROM task_executions WHERE task_key = 'collect' "
                    "AND run_id = :run_id"
                ),
                {"run_id": task_run_id},
            )
            self.assertEqual(persisted_id, task_execution_id)
            persisted_row = await connection.execute(
                text(
                    "SELECT status, started_at, completed_at FROM task_executions "
                    "WHERE id = :task_execution_id"
                ),
                {"task_execution_id": task_execution_id},
            )
            status, started_at, completed_at = persisted_row.one()
            self.assertEqual(status, "completed")
            self.assertIsNotNone(started_at.tzinfo)
            self.assertIsNotNone(completed_at.tzinfo)

    async def test_protected_api_rejects_unauthenticated_requests_without_test_bypass(self) -> None:
        self.assertEqual(app.dependency_overrides, {})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/runs")
        self.assertEqual(response.status_code, 401)
