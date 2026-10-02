"""Small real-adapter baseline; not a claim of full workflow E2E coverage."""

import asyncio
import os
import unittest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
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
from app.infrastructure.postgres.conversation_repository import PostgresConversationRepository  # noqa: E402
from app.infrastructure.postgres.run_repository import PostgresRunRepository  # noqa: E402
from app.infrastructure.postgres.workflow_repository import PostgresWorkflowRepository  # noqa: E402
from app.infrastructure.redis.run_queue import RedisRunCommandQueue  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.runs.models import RunDocument  # noqa: E402
from app.modules.conversations.models import ConversationMessage, ConversationRecord, ConversationTurn  # noqa: E402
from app.shared.commands import RunCommand  # noqa: E402
from app.shared.events import ExecutionEvent  # noqa: E402
from app.shared.errors import ConflictError  # noqa: E402


def setUpModule() -> None:
    require_integration_environment()
    if settings.POSTGRES_URL != os.environ["POSTGRES_URL"] or settings.REDIS_URL != os.environ["REDIS_URL"]:
        raise RuntimeError("Settings do not match isolated integration targets.")
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestRealPersistence(unittest.IsolatedAsyncioTestCase):
    async def test_raw_sources_and_claims_persist_with_exact_provenance(self) -> None:
        from app.execution.research_contracts import EvidenceClaim, ResearchResult, SourceDocument
        from app.infrastructure.artifacts.evidence_store import DurableEvidenceStore
        from app.infrastructure.postgres.results_repository import PostgresResearchRepository

        repository = PostgresResearchRepository()
        self.assertFalse(repository.use_memory)
        excerpt = "Model Alpha scored 62.2%."
        document = SourceDocument(document_id="fixture-doc", run_id=self.run_id, task_id="1",
            source_url="https://fixture.invalid/paper", content_hash="fixture-hash", text=excerpt)
        claim = EvidenceClaim(evidence_id="fixture-claim", document_id=document.document_id, chunk_id="fixture-chunk",
            source_url=document.source_url, claim=excerpt, excerpt=excerpt, value_text="62.2", unit="%",
            start_offset=0, end_offset=len(excerpt))
        with tempfile.TemporaryDirectory() as root:
            store = DurableEvidenceStore(repository, root, self.repository)
            await store.save_document(document)
            await store.save_bundle(self.run_id, "1", ResearchResult(claims=[claim], status="complete"))
            await store.save_bundle(self.run_id, "1", ResearchResult(claims=[claim], status="complete"))
            saved_claims = await repository.list_evidence(self.run_id)
            self.assertEqual(len(saved_claims), 1)
            self.assertEqual(saved_claims[0].excerpt, excerpt)
            self.assertEqual(saved_claims[0].metadata["value_text"], "62.2")
            self.assertIsNotNone(saved_claims[0].collected_at.tzinfo)
            records = await repository.list_results(self.run_id)
            source = next(record for record in records if record.metadata.get("kind") == "source_document")
            self.assertEqual((Path(root) / source.content["storage_uri"]).read_text(encoding="utf-8"), excerpt)
            self.assertNotIn("text", source.content)
            self.assertIsNone(await self.repository.get(self.run_id, "another-owner"))

    async def test_http_fallback_persists_normalized_source_and_verified_claim(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from app.execution.research_contracts import ChunkExtraction, ExtractedClaim
        from app.execution.research_evidence import EvidenceProcessor
        from app.execution.tools.contracts import SourceMetadata, success_result
        from app.infrastructure.artifacts.evidence_store import DurableEvidenceStore
        from app.infrastructure.postgres.results_repository import PostgresResearchRepository

        url = "https://fixture.invalid/redirected-paper"
        excerpt = "Model Alpha scored 62.2%."
        llm = MagicMock()
        llm.model = "mock-model"
        llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=ChunkExtraction(claims=[
            ExtractedClaim(claim=excerpt, excerpt=excerpt, value_text="62.2", unit="%")]))
        repository = PostgresResearchRepository()
        with tempfile.TemporaryDirectory() as root:
            processor = EvidenceProcessor(llm, DurableEvidenceStore(repository, root, self.repository),
                                          self.run_id, "1")
            await processor.process_http(success_result({"body": f"<main><p>{excerpt}</p></main>"},
                tool_name="http_request", source=SourceMetadata(requested_url="https://fixture.invalid/start",
                    final_url=url, content_type="text/html", status_code=200)), "Find the score")
            self.assertEqual(processor.bundle.status, "complete")
            claims = await repository.list_evidence(self.run_id)
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0].source_url, url)
            self.assertEqual(claims[0].metadata["value_text"], "62.2")
            source = next(record for record in await repository.list_results(self.run_id)
                          if record.metadata.get("kind") == "source_document")
            text = (Path(root) / source.content["storage_uri"]).read_text(encoding="utf-8")
            claim = processor.bundle.claims[0]
            self.assertEqual(text[claim.start_offset:claim.end_offset], excerpt)
            self.assertNotIn("<main>", text)

    async def test_span_validation_persists_source_numeric_spelling_and_entity_context(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from app.execution.research_contracts import ChunkExtraction, ExtractedClaim
        from app.execution.research_evidence import EvidenceProcessor
        from app.execution.tools.contracts import SourceMetadata, success_result
        from app.infrastructure.artifacts.evidence_store import DurableEvidenceStore
        from app.infrastructure.postgres.results_repository import PostgresResearchRepository

        subject = "Qwen3-4B-Instruct-2507"
        excerpt = "Context Length: 262,144 natively ."
        model = MagicMock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=ChunkExtraction(claims=[
            ExtractedClaim(claim=f"{subject} supports 262144 natively", subject=subject,
                excerpt="Incorrect model paraphrase", source_span_id="s1", value_text="262144")]))
        repository = PostgresResearchRepository()
        with tempfile.TemporaryDirectory() as root:
            processor = EvidenceProcessor(model, DurableEvidenceStore(repository, root, self.repository),
                                          self.run_id, "1")
            await processor.process(success_result({"text": subject + "\n\n" + excerpt},
                source=SourceMetadata(final_url="https://fixture.invalid/card")), "Find context length")
            self.assertEqual(processor.bundle.status, "complete")
            claims = await repository.list_evidence(self.run_id)
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0].excerpt, excerpt)
            self.assertEqual(claims[0].metadata["value_text"], "262,144")
            records = await repository.list_results(self.run_id)
            bundle = next(record for record in records if record.metadata.get("kind") == "evidence_bundle")
            self.assertEqual(bundle.content["claims"][0]["subject"], subject)
            self.assertEqual(bundle.content["claims"][0]["claim"], excerpt)

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
                    "conversation_turns", "conversation_turn_events",
                }
                <= set(tables)
            )
            self.assertEqual(await connection.scalar(text("SELECT version_num FROM alembic_version")),
                             "0005_durable_conversation_turns")

    async def test_conversation_turn_acceptance_is_idempotent_serialized_and_replayable(self) -> None:
        repository = PostgresConversationRepository()
        now = datetime.now(timezone.utc)
        conversation = ConversationRecord(
            id=str(uuid4()),
            user_id=self.owner,
            title="Conversation turn integration",
            created_at=now,
            updated_at=now,
        )
        await repository.create(conversation)
        self.addAsyncCleanup(repository.delete, conversation.id, self.owner)

        candidates = []
        for request_text in ("first request", "competing request"):
            turn_id = str(uuid4())
            user_message = ConversationMessage(
                id=str(uuid4()),
                conversation_id=conversation.id,
                role="user",
                content=request_text,
                metadata={"turn_id": turn_id},
                created_at=now,
            )
            assistant_message = ConversationMessage(
                id=str(uuid4()),
                conversation_id=conversation.id,
                role="assistant",
                content="",
                metadata={"turn_id": turn_id, "status": "queued"},
                created_at=now + timedelta(microseconds=1),
            )
            turn = ConversationTurn(
                id=turn_id,
                conversation_id=conversation.id,
                user_id=self.owner,
                user_message_id=user_message.id,
                assistant_message_id=assistant_message.id,
                input_fingerprint=("a" if request_text == "first request" else "b") * 64,
                created_at=now,
            )
            candidates.append((turn, user_message, assistant_message))

        async def accept(candidate):
            turn, user_message, assistant_message = candidate
            return await repository.accept_turn(
                conversation,
                turn,
                user_message,
                assistant_message,
            )

        outcomes = await asyncio.gather(*(accept(candidate) for candidate in candidates), return_exceptions=True)
        accepted = [item for item in outcomes if isinstance(item, ConversationTurn)]
        conflicts = [item for item in outcomes if isinstance(item, ConflictError)]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(conflicts), 1)
        winner_index = next(index for index, candidate in enumerate(candidates) if candidate[0].id == accepted[0].id)
        turn, user_message, assistant_message = candidates[winner_index]

        retry = await repository.accept_turn(conversation, turn, user_message, assistant_message)
        self.assertEqual(retry.id, turn.id)
        self.assertEqual(len(await repository.list_messages(conversation.id, limit=10)), 2)
        self.assertIsNone(await repository.get_turn(conversation.id, turn.id, "another-owner"))
        claimed = await repository.claim_next_turn("integration-worker")
        self.assertEqual(claimed.status, "running")
        events = await repository.list_turn_events(conversation.id, turn.id)
        self.assertEqual([event.sequence for event in events], [1, 2])
        self.assertEqual([event.type for event in events], ["turn_accepted", "planning_started"])

    async def test_finalizing_turn_keeps_sequences_written_during_streaming(self) -> None:
        repository = PostgresConversationRepository()
        now = datetime.now(timezone.utc)
        conversation = ConversationRecord(
            id=str(uuid4()),
            user_id=self.owner,
            title="Streaming turn integration",
            created_at=now,
            updated_at=now,
        )
        await repository.create(conversation)
        self.addAsyncCleanup(repository.delete, conversation.id, self.owner)

        turn_id = str(uuid4())
        user_message = ConversationMessage(
            id=str(uuid4()), conversation_id=conversation.id, role="user",
            content="Summarize this", metadata={"turn_id": turn_id}, created_at=now,
        )
        assistant_message = ConversationMessage(
            id=str(uuid4()), conversation_id=conversation.id, role="assistant",
            content="", metadata={"turn_id": turn_id, "status": "queued"},
            created_at=now + timedelta(microseconds=1),
        )
        turn = ConversationTurn(
            id=turn_id, conversation_id=conversation.id, user_id=self.owner,
            user_message_id=user_message.id, assistant_message_id=assistant_message.id,
            input_fingerprint="a" * 64, created_at=now,
        )
        await repository.accept_turn(conversation, turn, user_message, assistant_message)
        claimed = await repository.claim_next_turn("integration-worker")
        self.assertEqual(claimed.id, turn_id)
        self.assertEqual(claimed.last_event_sequence, 2)

        await repository.append_turn_event(turn_id, "assistant_delta", {"content": "First "})
        await repository.append_turn_event(turn_id, "assistant_delta", {"content": "answer"})
        self.assertEqual(claimed.last_event_sequence, 2)
        await repository.save_turn(claimed.model_copy(deep=True))
        self.assertEqual(
            (await repository.get_turn(conversation.id, turn_id, self.owner)).last_event_sequence,
            4,
        )

        claimed.status = "completed"
        claimed.outcome = "answer"
        claimed.assistant_content = "First answer"
        claimed.completed_at = datetime.now(timezone.utc)
        assistant_message.content = claimed.assistant_content
        assistant_message.metadata = {"turn_id": turn_id, "status": "completed"}
        written = await repository.finalize_turn(
            claimed, conversation, assistant_message,
            [
                ("assistant_message", {"content": claimed.assistant_content}),
                ("turn_completed", {"status": "completed", "outcome": "answer"}),
            ],
        )

        events = await repository.list_turn_events(conversation.id, turn_id)
        self.assertEqual([event.sequence for event in events], [1, 2, 3, 4, 5, 6])
        self.assertEqual([event.sequence for event in written], [5, 6])
        self.assertEqual(claimed.last_event_sequence, 6)
        persisted = await repository.get_turn(conversation.id, turn_id, self.owner)
        self.assertEqual(persisted.status, "completed")
        self.assertEqual(persisted.last_event_sequence, 6)

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
