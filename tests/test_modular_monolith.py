import asyncio
import json
import os
import re
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List

from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.core.config import settings
from app.execution.model_router import InferencePurpose
from app.execution.state import State, Task
from app.modules.catalog.domain import AgentDefinition, ToolDefinition
from app.modules.catalog.service import CatalogService
from app.modules.conversations.events import ConversationEvent
from app.modules.conversations.models import ConversationMessage, ConversationRecord, ConversationTurn
from app.modules.conversations.service import ConversationService
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService
from app.shared.events import ExecutionEvent
from app.infrastructure.redis.event_publisher import InMemoryRunEventPublisher
from app.infrastructure.redis.run_queue import InMemoryRunCommandQueue
from app.workers.run_worker import RunWorker
from app.modules.workflows.domain import WorkflowRecord
from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.workflows.schemas import WorkflowCreateRequest
from app.modules.workflows.service import WorkflowService
from app.modules.workflows.validator import (
    validate_workflow_definition,
    validate_workflow_references,
)
from app.shared.errors import ValidationError


def make_task(
    task_id: int,
    dependencies: List[int] | None = None,
    status: str = "pending",
) -> Task:
    return Task(
        id=task_id,
        node=f"node_{task_id}",
        status=status,
        description=f"Task {task_id}",
        dependencies=dependencies or [],
    )


class FakeCatalogRepository:
    async def list_agents(self, active_only: bool = True) -> List[AgentDefinition]:
        return [
            AgentDefinition(
                id="agent-1",
                name="Researcher",
                system_prompt="Research carefully.",
            )
        ]

    async def list_tools(self, active_only: bool = True) -> List[ToolDefinition]:
        return [
            ToolDefinition(
                id="tool-1",
                name="web_search",
                description="Search the web.",
            )
        ]


class FakeWorkflowRepository:
    def __init__(self) -> None:
        self.records: Dict[str, WorkflowRecord] = {}

    async def create(
        self,
        name: str,
        description: str | None,
        user_id: str,
        definition: Dict[str, Any],
    ) -> WorkflowRecord:
        record = WorkflowRecord(
            id="workflow-1",
            name=name,
            description=description,
            user_id=user_id,
            definition=definition,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.records[record.id] = record
        return record

    async def list(self, user_id: str) -> List[WorkflowRecord]:
        return [record for record in self.records.values() if record.user_id == user_id]

    async def get(self, workflow_id: str, user_id: str) -> WorkflowRecord | None:
        record = self.records.get(workflow_id)
        return record if record and record.user_id == user_id else None

    async def get_definition(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> Dict[str, Any] | None:
        record = await self.get(workflow_id, user_id)
        return record.definition if record else None

    async def get_current_version(
        self,
        workflow_id: str,
        user_id: str = "default_user",
    ) -> WorkflowRecord | None:
        record = await self.get(workflow_id, user_id)
        return record if record and record.status == "active" else None

    async def get_version(self, workflow_id: str, version_id: str, user_id: str):
        record = await self.get(workflow_id, user_id)
        if record is None or record.version_id != version_id:
            return None
        from app.modules.workflows.domain import WorkflowVersionRecord

        return WorkflowVersionRecord(
            id=version_id,
            workflow_id=workflow_id,
            version_number=record.version_number or 1,
            status="published",
            definition=record.definition,
            created_by=user_id,
            created_at=record.updated_at,
        )


async def attach_published_version(
    service: RunService,
    document: RunDocument,
    definition: Dict[str, Any] | None = None,
) -> str:
    version_id = f"version-{document.run_id}"
    version_repository = FakeWorkflowRepository()
    version_definition = definition or {
        "tasks": [task.model_dump(mode="json") for task in document.plan]
    }
    now = datetime.now(timezone.utc)
    version_repository.records[document.flow_id] = WorkflowRecord(
        id=document.flow_id,
        version_id=version_id,
        version_number=1,
        name="Approved workflow",
        user_id=document.user_id,
        definition=version_definition,
        created_at=now,
        updated_at=now,
    )
    document.workflow_version_id = version_id
    document.plan_revision = RunService._plan_revision(document.plan)
    document.approved_plan_revision = RunService._plan_revision(document.plan)
    await service.run_repository.save(document)
    service.workflow_repository = version_repository
    return version_id


class FakeRunRepository:
    def __init__(self) -> None:
        self.documents: Dict[str, RunDocument] = {}
        self.events: Dict[str, List[ExecutionEvent]] = {}

    async def save(self, document: RunDocument) -> None:
        self.documents[document.run_id] = document.model_copy(deep=True)

    async def save_if_plan_revision_matches(
        self,
        document: RunDocument,
        expected_revision: str,
        expected_updated_at,
        allowed_statuses,
    ) -> bool:
        current = self.documents.get(document.run_id)
        if (
            current is None
            or current.user_id != document.user_id
            or current.plan_revision != expected_revision
            or current.updated_at != expected_updated_at
            or current.status not in set(allowed_statuses)
        ):
            return False
        self.documents[document.run_id] = document.model_copy(deep=True)
        return True

    async def get(self, run_id: str) -> RunDocument | None:
        document = self.documents.get(run_id)
        return document.model_copy(deep=True) if document else None

    async def list(self, flow_id: str | None = None, limit: int = 50) -> List[RunDocument]:
        values = list(self.documents.values())
        if flow_id:
            values = [document for document in values if document.flow_id == flow_id]
        return values[:limit]

    async def find_by_idempotency_key(
        self,
        idempotency_key: str,
        user_id: str,
    ) -> RunDocument | None:
        document = next(
            (
                item
                for item in self.documents.values()
                if item.idempotency_key == idempotency_key and item.user_id == user_id
            ),
            None,
        )
        return document.model_copy(deep=True) if document else None

    async def claim(self, run_id: str) -> RunDocument | None:
        document = self.documents.get(run_id)
        if document is None or document.status != "queued":
            return None
        document.status = "running"
        return document.model_copy(deep=True)

    async def append_event(self, event: ExecutionEvent) -> ExecutionEvent:
        events = self.events.setdefault(event.run_id, [])
        persisted = event.model_copy(update={"sequence": len(events) + 1})
        events.append(persisted)
        return persisted

    async def list_events(
        self,
        run_id: str,
        after_event_id: str | None = None,
        limit: int = 200,
    ) -> List[ExecutionEvent]:
        events = self.events.get(run_id, [])
        if after_event_id:
            for index, event in enumerate(events):
                if event.event_id == after_event_id:
                    events = events[index + 1 :]
                    break
        return events[:limit]


class FakeExecutionPort:
    def __init__(self) -> None:
        self.created_plan_for: str | None = None
        self.initial_state: State | None = None
        self.continuation_metadata: Dict[str, Any] | None = None

    async def create_plan(
        self,
        run_id: str,
        initial_state: State,
        on_assistant_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> State:
        self.created_plan_for = run_id
        self.initial_state = initial_state
        if on_assistant_token:
            await on_assistant_token("I drafted ")
            await on_assistant_token("a workflow for review.")
        return {
            "mode": "conversation",
            "plan": [make_task(1)],
            "messages": [AIMessage(content="I drafted a workflow for review.")],
            "logs": ["plan created by fake execution adapter"],
            "result_storage": [],
            "metadata": {"supervisor_decision": "propose_plan"},
        }

    async def continue_conversation(
        self,
        run_id: str,
        message: str,
        metadata: Dict[str, Any] | None = None,
        on_assistant_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> State:
        self.continuation_metadata = dict(metadata or {})
        if on_assistant_token:
            await on_assistant_token("I updated ")
            await on_assistant_token("the workflow for review.")
        return {
            "mode": "conversation",
            "plan": [make_task(1)],
            "messages": [AIMessage(content="I updated the workflow for review.")],
            "logs": [f"received: {message}"],
            "metadata": {"supervisor_decision": "propose_plan"},
        }

    async def _stream(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        yield {
            "worker_node": {
                "logs": ["worker completed"],
                "plan": [make_task(1, status="done")],
                "result_storage": [
                    {
                        "task_id": 1,
                        "result": "done",
                        "status": "done",
                    }
                ],
            }
        }

    def stream_execution(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        return self._stream(run_id)

    def execute_run(
        self,
        run_id: str,
        initial_state: State,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        return self._stream(run_id)


class FailedExecutionPort(FakeExecutionPort):
    async def _stream(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        yield {
            "worker_node": {
                "logs": ["worker failed"],
                "plan": [
                    make_task(
                        1,
                        status="failed",
                    ).model_copy(update={"error": "source unavailable"})
                ],
                "result_storage": [
                    {
                        "task_id": 1,
                        "result": "",
                        "status": "failed",
                        "error": "source unavailable",
                    }
                ],
            }
        }


class PartialExecutionPort(FakeExecutionPort):
    async def _stream(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        partial_task = make_task(1, status="partial").model_copy(
            update={"error": "One research query returned no results."}
        )
        yield {
            "worker_node": {
                "logs": ["Source research completed with one missing query."],
                "plan": [partial_task],
                "result_storage": [{
                    "task_id": 1,
                    "result": "Evidence collected. Coverage warning: one query failed.",
                    "status": "partial",
                    "error": partial_task.error,
                }],
            }
        }
        report_task = make_task(2, dependencies=[1], status="done")
        yield {
            "worker_node": {
                "logs": ["Report created from available evidence."],
                "plan": [report_task],
                "result_storage": [{
                    "task_id": 2,
                    "result": "Report created with a source coverage caveat.",
                    "status": "done",
                }],
            }
        }


class TestWorkflowBoundaries(unittest.IsolatedAsyncioTestCase):
    def test_modules_do_not_depend_on_infrastructure_or_sqlalchemy(self) -> None:
        modules_root = Path(__file__).resolve().parents[1] / "backend" / "app" / "modules"
        for source_path in modules_root.rglob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"^\s*(from|import)\s+sqlalchemy", source, re.MULTILINE),
                str(source_path),
            )
            self.assertIsNone(
                re.search(r"^\s*(from|import)\s+app\.infrastructure", source, re.MULTILINE),
                str(source_path),
            )

    def test_workflow_validator_rejects_invalid_dag(self) -> None:
        from app.execution.state import FlowDefinition

        with self.assertRaises(ValidationError):
            validate_workflow_definition(
                FlowDefinition(
                    flow_id="invalid",
                    name="Duplicate IDs",
                    tasks=[make_task(1), make_task(1)],
                )
            )

        with self.assertRaises(ValidationError):
            validate_workflow_definition(
                FlowDefinition(
                    flow_id="invalid",
                    name="Missing dependency",
                    tasks=[make_task(1, dependencies=[99])],
                )
            )

        with self.assertRaises(ValidationError):
            validate_workflow_definition(
                FlowDefinition(
                    flow_id="invalid",
                    name="Cycle",
                    tasks=[make_task(1, dependencies=[2]), make_task(2, dependencies=[1])],
                )
            )

    async def test_application_services_depend_on_ports(self) -> None:
        catalog = CatalogService(repository=FakeCatalogRepository())
        self.assertEqual((await catalog.list_tools())[0].name, "web_search")

        workflows = WorkflowService(repository=FakeWorkflowRepository())
        request = WorkflowCreateRequest(
            name="Research workflow",
            definition={
                "flow_id": "workflow-1",
                "name": "Research workflow",
                "tasks": [make_task(1).model_dump()],
            },
        )
        record = await workflows.create_workflow(request)
        self.assertEqual(record.name, "Research workflow")

    async def test_catalog_reports_tools_blocked_by_deployment_policy(self) -> None:
        class PolicyCatalogRepository(FakeCatalogRepository):
            async def list_agents(self, active_only: bool = True) -> List[AgentDefinition]:
                return [AgentDefinition(
                    id="agent-report",
                    name="report_agent",
                    system_prompt="Write reports.",
                    tool_names=["python_executor", "file_writer"],
                )]

            async def list_tools(self, active_only: bool = True) -> List[ToolDefinition]:
                return [
                    ToolDefinition(id="tool-python", name="python_executor"),
                    ToolDefinition(id="tool-writer", name="file_writer"),
                ]

        with (
            patch.object(settings, "ENABLE_UNSANDBOXED_PYTHON_EXECUTION", False),
            patch.object(settings, "ENABLE_EXTERNAL_SIDE_EFFECT_TOOLS", False),
        ):
            catalog = CatalogService(repository=PolicyCatalogRepository())
            tool_map = {tool.name: tool for tool in await catalog.list_tools()}
            agent = (await catalog.list_agents())[0]

        self.assertFalse(tool_map["python_executor"].is_available)
        self.assertIn("not an OS sandbox", tool_map["python_executor"].unavailable_reason)
        self.assertTrue(tool_map["file_writer"].is_available)
        self.assertEqual(agent.available_tool_names, ["file_writer"])
        self.assertEqual(agent.blocked_tool_names, ["python_executor"])

    async def test_workflow_tool_overrides_are_resolved_and_authorized(self) -> None:
        class TwoToolCatalog(FakeCatalogRepository):
            async def list_agents(self, active_only: bool = True) -> List[AgentDefinition]:
                return [AgentDefinition(
                    id="agent-1",
                    name="Researcher",
                    system_prompt="Research carefully.",
                    tool_names=["web_search", "file_writer"],
                )]

            async def list_tools(self, active_only: bool = True) -> List[ToolDefinition]:
                return [
                    ToolDefinition(id="tool-search", name="web_search"),
                    ToolDefinition(id="tool-writer", name="file_writer"),
                ]

        definition = normalize_workflow_definition({
            "steps": [{
                "task_key": "collect",
                "name": "Collect sources",
                "description": "Find sources",
                "agent_id": "Researcher",
                "dependencies": [],
                "config": {"tool_names": ["web_search"]},
            }]
        })
        with patch.dict(os.environ, {"TESTING": "false"}):
            resolved = await validate_workflow_references(definition, TwoToolCatalog())

        self.assertEqual(resolved["steps"][0]["tool_names"], ["web_search"])
        self.assertEqual(resolved["steps"][0]["tool_ids"], ["tool-search"])
        self.assertEqual(resolved["tasks"][0]["tool_names"], ["web_search"])

    async def test_terminal_event_storage_failure_does_not_rewrite_completed_run(self) -> None:
        class TerminalEventFailureRepository(FakeRunRepository):
            async def append_event(self, event: ExecutionEvent) -> ExecutionEvent:
                if event.type == "run_completed":
                    raise RuntimeError("terminal event table unavailable")
                return await super().append_event(event)

        repository = TerminalEventFailureRepository()
        service = RunService(
            run_repository=repository,
            workflow_repository=None,
            execution_port=FakeExecutionPort(),
            event_publisher=InMemoryRunEventPublisher(),
        )
        document = RunDocument(
            run_id="completed-event-write-failure",
            flow_id="workflow-1",
            status="queued",
            mode="executing",
            plan=[make_task(1)],
        )
        await repository.save(document)

        completed = await service.execute_queued_run(document.run_id)

        self.assertEqual(completed.status, "completed")
        self.assertEqual(repository.documents[document.run_id].status, "completed")
        self.assertFalse(
            any(event.type == "run_failed" for event in repository.events[document.run_id])
        )

    async def test_run_service_uses_ports_and_emits_event_ids(self) -> None:
        repository = FakeRunRepository()
        execution = FakeExecutionPort()
        service = RunService(
            run_repository=repository,
            workflow_repository=None,
            execution_port=execution,
        )

        document = await service.create_run(
            flow_id="flow-1",
            input_message="Research AI systems",
        )
        self.assertEqual(execution.created_plan_for, document.run_id)
        self.assertEqual(document.status, "pending")
        self.assertIsNone(document.workflow_version_id)
        await attach_published_version(service, document)

        await service.approve_run(document.run_id, plan_revision=document.plan_revision)
        await service.execute_queued_run(document.run_id)
        events = [
            json.loads(
                next(
                    line.removeprefix("data: ")
                    for line in event.splitlines()
                    if line.startswith("data: ")
                )
            )
            async for event in service.stream_run_events(document.run_id)
        ]

        self.assertTrue(events)
        self.assertTrue(all(event["event_id"] for event in events))
        self.assertTrue(all(event["run_id"] == document.run_id for event in events))
        self.assertEqual(events[-1]["type"], "run_completed")
        self.assertEqual(events[-1]["status"], "completed")

    async def test_worker_claims_and_executes_queued_run_once(self) -> None:
        workflow_repository = FakeWorkflowRepository()
        workflow_repository.records["workflow-1"] = WorkflowRecord(
            id="workflow-1",
            version_id="00000000-0000-0000-0000-000000000001",
            version_number=1,
            name="Research",
            user_id="default_user",
            definition={
                "flow_id": "workflow-1",
                "name": "Research",
                "tasks": [make_task(1).model_dump()],
            },
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        run_repository = FakeRunRepository()
        queue = InMemoryRunCommandQueue()
        service = RunService(
            run_repository=run_repository,
            workflow_repository=workflow_repository,
            execution_port=FakeExecutionPort(),
            command_queue=queue,
            event_publisher=InMemoryRunEventPublisher(),
        )

        document = await service.create_workflow_run(
            workflow_id="workflow-1",
            workflow_version_id="00000000-0000-0000-0000-000000000001",
            idempotency_key="request-1",
            conversation_id="conversation-1",
        )
        self.assertIsNotNone(document)
        self.assertEqual(document.status, "queued")
        self.assertEqual(document.conversation_id, "conversation-1")

        worker = RunWorker(service=service, command_queue=queue)
        self.assertTrue(await worker.process_next(timeout=1))
        completed = await service.get_run(document.run_id)
        self.assertEqual(completed.status, "completed")
        self.assertFalse(await worker.process_next(timeout=0))

        same_document = await service.create_workflow_run(
            workflow_id="workflow-1",
            workflow_version_id="00000000-0000-0000-0000-000000000001",
            input_data={},
            conversation_id="conversation-1",
            idempotency_key="request-1",
        )
        self.assertEqual(same_document.run_id, document.run_id)

        with self.assertRaisesRegex(Exception, "different run request"):
            await service.create_workflow_run(
                workflow_id="workflow-1",
                workflow_version_id="00000000-0000-0000-0000-000000000001",
                input_data={"query": "different request"},
                conversation_id="conversation-1",
                idempotency_key="request-1",
            )

    async def test_approval_requires_the_exact_reviewed_plan_revision(self) -> None:
        repository = FakeRunRepository()
        service = RunService(
            run_repository=repository,
            workflow_repository=None,
            execution_port=FakeExecutionPort(),
        )
        document = await service.create_run("workflow-approval")

        with self.assertRaisesRegex(Exception, "Save and publish"):
            await service.approve_run(document.run_id, plan_revision=document.plan_revision)

        await attach_published_version(service, document)
        with self.assertRaisesRegex(Exception, "revision is required"):
            await service.approve_run(document.run_id)

        changed_revision = "0" * 64
        with self.assertRaisesRegex(Exception, "changed after it was reviewed"):
            await service.approve_run(document.run_id, plan_revision=changed_revision)

        approved = await service.approve_run(document.run_id, plan_revision=document.plan_revision)
        self.assertEqual(approved.approved_plan_revision, document.plan_revision)
        self.assertEqual(approved.status, "queued")

    async def test_approval_rejects_plan_that_differs_from_selected_version(self) -> None:
        repository = FakeRunRepository()
        service = RunService(
            run_repository=repository,
            workflow_repository=None,
            execution_port=FakeExecutionPort(),
        )
        document = await service.create_run("workflow-version-mismatch")
        changed_task = make_task(1).model_copy(update={"description": "Different authored task"})
        await attach_published_version(
            service,
            document,
            {"tasks": [changed_task.model_dump(mode="json")]},
        )

        with self.assertRaisesRegex(Exception, "does not match the selected published workflow version"):
            await service.approve_run(
                document.run_id,
                plan_revision=document.plan_revision,
            )

    async def test_execution_worker_fails_closed_for_unversioned_queued_run(self) -> None:
        repository = FakeRunRepository()
        service = RunService(
            run_repository=repository,
            workflow_repository=FakeWorkflowRepository(),
            execution_port=FakeExecutionPort(),
            event_publisher=InMemoryRunEventPublisher(),
        )
        document = RunDocument(
            run_id="unversioned-queued-run",
            flow_id="workflow-unversioned",
            status="queued",
            mode="executing",
            plan=[make_task(1)],
        )
        await repository.save(document)

        failed = await service.execute_queued_run(document.run_id)

        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "approved_workflow_snapshot_invalid")

    async def test_plan_update_rejects_a_stale_run_snapshot_even_if_plan_hash_matches(self) -> None:
        repository = FakeRunRepository()
        service = RunService(
            run_repository=repository,
            workflow_repository=None,
            execution_port=FakeExecutionPort(),
        )
        document = await service.create_run("workflow-stale-snapshot")
        expected_updated_at = document.updated_at
        concurrent_update = document.model_copy(deep=True)
        concurrent_update.updated_at += timedelta(seconds=1)
        await repository.save(concurrent_update)

        document.logs.append("stale write")
        self.assertFalse(
            await repository.save_if_plan_revision_matches(
                document,
                document.plan_revision,
                expected_updated_at,
                ("pending", "created", "waiting_for_approval", "paused"),
            )
        )
        self.assertNotIn("stale write", repository.documents[document.run_id].logs)

    async def test_failed_task_marks_run_failed(self) -> None:
        repository = FakeRunRepository()
        service = RunService(
            run_repository=repository,
            workflow_repository=None,
            execution_port=FailedExecutionPort(),
            event_publisher=InMemoryRunEventPublisher(),
        )
        document = RunDocument(
            run_id="failed-task-run",
            flow_id="workflow-1",
            status="queued",
            mode="executing",
            plan=[make_task(1)],
        )
        await repository.save(document)

        completed = await service.execute_queued_run(document.run_id)

        self.assertEqual(completed.status, "failed")
        self.assertEqual(completed.error_code, "task_execution_failed")
        self.assertEqual(repository.events[document.run_id][-1].type, "run_failed")

    async def test_partial_source_task_can_finish_run_and_is_reported(self) -> None:
        repository = FakeRunRepository()
        service = RunService(
            run_repository=repository,
            workflow_repository=None,
            execution_port=PartialExecutionPort(),
            event_publisher=InMemoryRunEventPublisher(),
        )
        document = RunDocument(
            run_id="partial-source-run",
            flow_id="workflow-1",
            status="queued",
            mode="executing",
            plan=[make_task(1), make_task(2, dependencies=[1])],
        )
        await repository.save(document)

        completed = await service.execute_queued_run(document.run_id)

        self.assertEqual(completed.status, "completed")
        self.assertEqual([task.status for task in completed.plan], ["partial", "done"])
        self.assertTrue(completed.metadata["partial_completion"])
        self.assertTrue(completed.metadata["has_partial_results"])
        self.assertEqual(completed.metadata["partial_task_ids"], [1])
        self.assertEqual(repository.events[document.run_id][-1].type, "run_completed")


class FakeConversationRepository:
    def __init__(self) -> None:
        self.conversations: Dict[str, ConversationRecord] = {}
        self.messages: Dict[str, List[ConversationMessage]] = {}
        self.turns: Dict[str, ConversationTurn] = {}
        self.turn_events: Dict[str, List[ConversationEvent]] = {}

    async def create(self, conversation: ConversationRecord) -> ConversationRecord:
        self.conversations[conversation.id] = conversation
        return conversation

    async def get(self, conversation_id: str, user_id: str) -> ConversationRecord | None:
        conversation = self.conversations.get(conversation_id)
        return conversation if conversation and conversation.user_id == user_id else None

    async def list(self, user_id: str, limit: int = 50) -> List[ConversationRecord]:
        return [c for c in self.conversations.values() if c.user_id == user_id][:limit]

    async def delete(self, conversation_id: str, user_id: str) -> bool:
        conversation = await self.get(conversation_id, user_id)
        if conversation is None:
            return False
        self.conversations.pop(conversation_id, None)
        self.messages.pop(conversation_id, None)
        for turn_id, turn in list(self.turns.items()):
            if turn.conversation_id == conversation_id:
                self.turns.pop(turn_id, None)
                self.turn_events.pop(turn_id, None)
        return True

    async def save(self, conversation: ConversationRecord) -> ConversationRecord:
        self.conversations[conversation.id] = conversation
        return conversation

    async def add_message(self, message: ConversationMessage) -> ConversationMessage:
        self.messages.setdefault(message.conversation_id, []).append(message)
        return message

    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 200,
    ) -> List[ConversationMessage]:
        return self.messages.get(conversation_id, [])[:limit]

    async def accept_turn(self, conversation, turn, user_message, assistant_message):
        existing = self.turns.get(turn.id)
        if existing:
            if existing.input_fingerprint == turn.input_fingerprint and existing.user_id == turn.user_id:
                return existing
            from app.shared.errors import ConflictError
            raise ConflictError("Turn identifier was reused for a different request.")
        if any(item.conversation_id == turn.conversation_id and item.status in {"queued", "running", "cancel_requested"}
               for item in self.turns.values()):
            from app.shared.errors import ConflictError
            raise ConflictError("Conversation already has an active turn.")
        conversation.draft_plan = []
        conversation.metadata.pop("supervisor_decision", None)
        conversation.metadata.pop("last_turn", None)
        conversation.updated_at = turn.created_at
        self.conversations[conversation.id] = conversation
        self.messages.setdefault(conversation.id, []).extend([user_message, assistant_message])
        turn.last_event_sequence = 1
        self.turns[turn.id] = turn
        self.turn_events[turn.id] = [ConversationEvent(
            conversation_id=turn.conversation_id,
            turn_id=turn.id,
            sequence=1,
            type="turn_accepted",
            payload={"user_message_id": turn.user_message_id, "assistant_message_id": turn.assistant_message_id},
        )]
        return turn

    async def get_turn(self, conversation_id, turn_id, user_id):
        turn = self.turns.get(turn_id)
        return turn if turn and turn.conversation_id == conversation_id and turn.user_id == user_id else None

    async def list_turns(self, conversation_id, user_id, limit=50):
        return [turn for turn in self.turns.values()
                if turn.conversation_id == conversation_id and turn.user_id == user_id][:limit]

    async def claim_next_turn(self, worker_id):
        turn = next((item for item in self.turns.values() if item.status == "queued"), None)
        if turn is None:
            return None
        turn.status = "running"
        turn.worker_id = worker_id
        turn.started_at = datetime.now(timezone.utc)
        turn.heartbeat_at = turn.started_at
        await self.append_turn_event(turn.id, "planning_started", {"status": "running"})
        return turn

    async def heartbeat_turn(self, turn_id, worker_id):
        turn = self.turns.get(turn_id)
        if not turn or turn.worker_id != worker_id or turn.status != "running":
            return False
        turn.heartbeat_at = datetime.now(timezone.utc)
        return True

    async def save_turn(self, turn):
        self.turns[turn.id] = turn
        return turn

    async def save_message(self, message):
        messages = self.messages.setdefault(message.conversation_id, [])
        for index, existing in enumerate(messages):
            if existing.id == message.id:
                messages[index] = message
                return message
        messages.append(message)
        return message

    async def finalize_turn(self, turn, conversation, assistant_message, events):
        self.turns[turn.id] = turn
        self.conversations[conversation.id] = conversation
        await self.save_message(assistant_message)
        written = []
        for event_type, payload in events:
            written.append(await self.append_turn_event(turn.id, event_type, payload))
        return written

    async def request_turn_cancel(self, conversation_id, turn_id, user_id):
        turn = await self.get_turn(conversation_id, turn_id, user_id)
        if turn and turn.status in {"queued", "running"}:
            turn.status = "cancel_requested"
            await self.append_turn_event(turn.id, "turn_cancel_requested", {"status": turn.status})
        return turn

    async def append_turn_event(self, turn_id, event_type, payload):
        turn = self.turns[turn_id]
        turn.last_event_sequence += 1
        event = ConversationEvent(
            conversation_id=turn.conversation_id,
            turn_id=turn_id,
            sequence=turn.last_event_sequence,
            type=event_type,
            payload=payload,
        )
        self.turn_events.setdefault(turn_id, []).append(event)
        return event

    async def list_turn_events(self, conversation_id, turn_id, after_sequence=0, limit=500):
        return [event for event in self.turn_events.get(turn_id, [])
                if event.conversation_id == conversation_id and event.sequence > after_sequence][:limit]

    async def recover_stale_turns(self, stale_before, queue_expired_before):
        return []


class TestConversationBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_persists_user_message_and_draft_plan(self) -> None:
        repository = FakeConversationRepository()
        service = ConversationService(
            repository=repository,
            execution_port=FakeExecutionPort(),
        )
        conversation = await service.create_conversation(title="Research chat")
        updated = await service.send_message(
            conversation_id=conversation.id,
            content="Research local LLMs",
        )

        self.assertEqual(updated.status, "waiting_for_user")
        self.assertEqual(len(updated.draft_plan), 1)
        self.assertTrue(updated.metadata["use_llm"])
        self.assertEqual(updated.metadata["inference_purpose"], InferencePurpose.PLANNER.value)
        self.assertNotIn("model_name", updated.metadata)
        self.assertTrue(service.execution_port.initial_state["metadata"]["use_llm"])
        messages = await service.list_messages(conversation.id)
        self.assertEqual(messages[0].role, "user")

    async def test_conversation_routes_plan_and_follow_up_to_planner(self) -> None:
        repository = FakeConversationRepository()
        execution_port = FakeExecutionPort()
        service = ConversationService(repository=repository, execution_port=execution_port)
        conversation = await service.create_conversation(title="Model selection chat")

        updated = await service.send_message(
            conversation_id=conversation.id,
            content="Research local LLMs",
        )
        self.assertEqual(updated.metadata["inference_purpose"], InferencePurpose.PLANNER.value)

        await service.send_message(
            conversation_id=conversation.id,
            content="Focus on context length",
        )
        self.assertTrue(execution_port.continuation_metadata["use_llm"])
        self.assertEqual(
            execution_port.continuation_metadata["inference_purpose"],
            InferencePurpose.PLANNER.value,
        )

    async def test_conversation_routes_simple_question_to_chat_profile(self) -> None:
        repository = FakeConversationRepository()
        execution_port = FakeExecutionPort()
        service = ConversationService(repository=repository, execution_port=execution_port)
        conversation = await service.create_conversation(title="Simple question")

        await service.send_message(conversation.id, "SSE là gì?")

        self.assertEqual(
            execution_port.initial_state["metadata"]["inference_purpose"],
            InferencePurpose.CHAT.value,
        )

    async def test_async_message_publishes_replayable_progress_events(self) -> None:
        from app.infrastructure.redis.conversation_event_publisher import (
            InMemoryConversationEventPublisher,
        )

        repository = FakeConversationRepository()
        publisher = InMemoryConversationEventPublisher()
        execution_port = FakeExecutionPort()
        service = ConversationService(
            repository=repository,
            execution_port=execution_port,
            event_publisher=publisher,
        )
        conversation = await service.create_conversation(title="Async research chat")
        accepted = await service.start_message(
            conversation_id=conversation.id,
            content="Research local LLMs",
        )

        self.assertEqual(accepted["status"], "accepted")
        self.assertTrue(await service.process_next_turn(worker_id="test-worker"))
        events = []
        async for frame in service.stream_events(
            conversation.id,
            turn_id=accepted["turn_id"],
        ):
            events.append(frame)

        self.assertIn("planning_started", "".join(events))
        self.assertIn("workflow_draft", "".join(events))
        self.assertIn("turn_completed", "".join(events))
        self.assertIn('"type": "stream_ready"', "".join(events))
        self.assertIn('"outcome": "propose_plan"', "".join(events))
        self.assertIn('"content": "I drafted a workflow for review."', "".join(events))
        self.assertTrue(execution_port.initial_state["metadata"]["use_llm"])
        self.assertEqual(
            execution_port.initial_state["metadata"]["inference_purpose"],
            InferencePurpose.PLANNER.value,
        )
        persisted_conversation = await service.get_conversation(conversation.id)
        self.assertEqual(len(persisted_conversation.metadata["chat_ttft_samples"]), 1)

    async def test_turn_submission_is_idempotent_and_conversation_serialized(self) -> None:
        from app.shared.errors import ConflictError

        repository = FakeConversationRepository()
        service = ConversationService(repository, FakeExecutionPort())
        conversation = await service.create_conversation(title="Idempotency")
        turn_id = "same-request-id"
        first = await service.start_message(conversation.id, "Research local LLMs", turn_id=turn_id)
        retry = await service.start_message(conversation.id, "Research local LLMs", turn_id=turn_id)

        self.assertEqual(first["user_message_id"], retry["user_message_id"])
        self.assertEqual(first["assistant_message_id"], retry["assistant_message_id"])
        self.assertEqual(len(await service.list_messages(conversation.id)), 2)
        with self.assertRaises(ConflictError):
            await service.start_message(conversation.id, "A second concurrent request")

    async def test_new_turn_clears_previous_draft_before_worker_can_claim_it(self) -> None:
        repository = FakeConversationRepository()
        service = ConversationService(repository, FakeExecutionPort())
        conversation = await service.create_conversation(title="No stale draft")
        conversation.draft_plan = [make_task(8)]
        conversation.metadata["supervisor_decision"] = "propose_plan"
        await repository.save(conversation)

        await service.start_message(conversation.id, "Research a different topic")
        refreshed = await service.get_conversation(conversation.id)

        self.assertEqual(refreshed.draft_plan, [])
        self.assertNotIn("supervisor_decision", refreshed.metadata)

    async def test_cancelling_queued_turn_is_terminal_and_replayable(self) -> None:
        repository = FakeConversationRepository()
        service = ConversationService(repository, FakeExecutionPort())
        conversation = await service.create_conversation(title="Cancel queued")
        accepted = await service.start_message(conversation.id, "Research something")

        turn = await service.cancel_turn(conversation.id, accepted["turn_id"])
        events = await repository.list_turn_events(conversation.id, turn.id)

        self.assertEqual(turn.status, "cancelled")
        self.assertEqual(events[-1].type, "turn_cancelled")
        self.assertEqual((await service.list_messages(conversation.id))[-1].content, "Yêu cầu đã được hủy.")

    async def test_cancelling_running_turn_interrupts_model_task_and_records_terminal_event(self) -> None:
        entered_model = asyncio.Event()

        class WaitingExecutionPort(FakeExecutionPort):
            async def create_plan(self, run_id, initial_state, on_assistant_token=None):
                entered_model.set()
                await asyncio.Event().wait()

        repository = FakeConversationRepository()
        service = ConversationService(repository, WaitingExecutionPort())
        conversation = await service.create_conversation(title="Cancel active")
        accepted = await service.start_message(conversation.id, "Research something")

        with patch.object(settings, "CONVERSATION_TURN_POLL_SECONDS", 0.01):
            worker_task = asyncio.create_task(service.process_next_turn(worker_id="test-worker"))
            await asyncio.wait_for(entered_model.wait(), timeout=1)
            await service.cancel_turn(conversation.id, accepted["turn_id"])
            await asyncio.wait_for(worker_task, timeout=1)

        turn = await repository.get_turn(conversation.id, accepted["turn_id"], "default_user")
        events = await repository.list_turn_events(conversation.id, turn.id)
        self.assertEqual(turn.status, "cancelled")
        self.assertEqual(events[-1].type, "turn_cancelled")

    async def test_sse_replay_cursor_skips_already_received_sequences(self) -> None:
        service = ConversationService(FakeConversationRepository(), FakeExecutionPort())
        conversation = await service.create_conversation(title="Replay cursor")
        accepted = await service.start_message(conversation.id, "Research local LLMs")
        await service.process_next_turn(worker_id="test-worker")

        frames = [
            frame
            async for frame in service.stream_events(
                conversation.id,
                turn_id=accepted["turn_id"],
                after_sequence=3,
            )
        ]
        self.assertNotIn("id: 1\n", "".join(frames))
        event_frames = [frame for frame in frames if '"type": "stream_ready"' not in frame]
        self.assertTrue(all(int(frame.splitlines()[0].removeprefix("id: ")) > 3 for frame in event_frames))
        self.assertEqual(frames[-1].splitlines()[0], "id: 6")

    async def test_ready_sse_subscriber_receives_first_streamed_assistant_chunk(self) -> None:
        from app.infrastructure.redis.conversation_event_publisher import (
            InMemoryConversationEventPublisher,
        )

        publisher = InMemoryConversationEventPublisher()
        service = ConversationService(
            repository=FakeConversationRepository(),
            execution_port=FakeExecutionPort(),
            event_publisher=publisher,
        )
        conversation = await service.create_conversation(title="Preconnected chat")
        turn_id = "preconnected-turn"
        event_stream = service.stream_events(conversation.id, turn_id=turn_id)

        ready_frame = await anext(event_stream)
        self.assertIn('"type": "stream_ready"', ready_frame)
        await service.start_message(
            conversation.id,
            "Research local models",
            turn_id=turn_id,
        )
        await service.process_next_turn(worker_id="test-worker")
        frames = [frame async for frame in event_stream]

        self.assertIn('"content": "I drafted a workflow for review."', "".join(frames))

    async def test_sse_without_fanout_still_signals_ready_before_turn_is_accepted(self) -> None:
        service = ConversationService(FakeConversationRepository(), FakeExecutionPort())
        conversation = await service.create_conversation(title="Database-only event stream")
        turn_id = "database-only-turn"
        event_stream = service.stream_events(conversation.id, turn_id=turn_id)

        ready_frame = await anext(event_stream)
        self.assertIn('"type": "stream_ready"', ready_frame)
        accepted = await service.start_message(
            conversation.id,
            "Research local models",
            turn_id=turn_id,
        )
        await service.process_next_turn(worker_id="test-worker")
        frames = [frame async for frame in event_stream]

        self.assertEqual(accepted["turn_id"], turn_id)
        self.assertIn('"type": "turn_completed"', "".join(frames))

    async def test_sse_falls_back_to_durable_polling_when_fanout_fails(self) -> None:
        class BrokenPublisher:
            async def publish(self, event):
                del event

            async def _broken_subscription(self):
                raise ConnectionError("Redis is unavailable")
                yield

            def subscribe(self, conversation_id, turn_id=None):
                del conversation_id, turn_id
                return self._broken_subscription()

        service = ConversationService(
            FakeConversationRepository(),
            FakeExecutionPort(),
            event_publisher=BrokenPublisher(),
        )
        conversation = await service.create_conversation(title="Redis outage")
        turn_id = "redis-outage-turn"
        event_stream = service.stream_events(conversation.id, turn_id=turn_id)

        ready_frame = await anext(event_stream)
        self.assertIn('"type": "stream_ready"', ready_frame)
        await service.start_message(conversation.id, "Research local models", turn_id=turn_id)
        await service.process_next_turn(worker_id="test-worker")
        frames = [frame async for frame in event_stream]

        self.assertIn('"type": "turn_completed"', "".join(frames))

    async def test_clarification_is_saved_and_the_next_message_continues_the_same_turn(self) -> None:
        class ClarifyingExecutionPort(FakeExecutionPort):
            async def create_plan(
                self,
                run_id: str,
                initial_state: State,
                on_assistant_token: Callable[[str], Awaitable[None]] | None = None,
            ) -> State:
                self.created_plan_for = run_id
                self.initial_state = initial_state
                return {
                    "mode": "conversation",
                    "plan": [],
                    "messages": [AIMessage(content="Bạn muốn nghiên cứu sản phẩm hay công ty nào?")],
                    "metadata": {"supervisor_decision": "clarify"},
                }

        repository = FakeConversationRepository()
        execution_port = ClarifyingExecutionPort()
        service = ConversationService(repository=repository, execution_port=execution_port)
        conversation = await service.create_conversation(title="Clarification chat")

        waiting = await service.send_message(conversation.id, "Research the latest launch")

        self.assertEqual(waiting.metadata["supervisor_decision"], "clarify")
        self.assertEqual(waiting.draft_plan, [])
        messages = await service.list_messages(conversation.id)
        self.assertEqual(messages[-1].content, "Bạn muốn nghiên cứu sản phẩm hay công ty nào?")

        clarified = await service.send_message(conversation.id, "A new laptop from ExampleCo")

        self.assertEqual(execution_port.continuation_metadata["supervisor_decision"], "clarify")
        self.assertEqual(clarified.metadata["supervisor_decision"], "propose_plan")
        self.assertEqual(len(clarified.draft_plan), 1)

    async def test_async_planning_error_publishes_failure_not_completion(self) -> None:
        from app.infrastructure.redis.conversation_event_publisher import (
            InMemoryConversationEventPublisher,
        )

        class FailingExecutionPort(FakeExecutionPort):
            async def _failed_result(self) -> State:
                return {
                    "mode": "conversation",
                    "messages": [AIMessage(content="Could not form a valid plan.")],
                    "metadata": {
                        "planning_failed": True,
                        "planning_error_message": "Invalid Supervisor response.",
                        "planning_error_id": "planner-error-1",
                        "planning_error_code": "supervisor_planning_failed",
                    },
                }

            async def create_plan(
                self,
                run_id: str,
                initial_state: State,
                on_assistant_token: Callable[[str], Awaitable[None]] | None = None,
            ) -> State:
                del run_id, initial_state
                del on_assistant_token
                return await self._failed_result()

            async def continue_conversation(
                self,
                run_id: str,
                message: str,
                metadata: Dict[str, Any] | None = None,
                on_assistant_token: Callable[[str], Awaitable[None]] | None = None,
            ) -> State:
                del run_id, message, metadata, on_assistant_token
                return await self._failed_result()

        repository = FakeConversationRepository()
        publisher = InMemoryConversationEventPublisher()
        service = ConversationService(
            repository=repository,
            execution_port=FailingExecutionPort(),
            event_publisher=publisher,
        )
        conversation = await service.create_conversation(title="Failed planning chat")
        conversation.draft_plan = [make_task(9)]
        conversation.metadata["supervisor_decision"] = "propose_plan"
        await repository.save(conversation)
        accepted = await service.start_message(
            conversation_id=conversation.id,
            content="Research something",
        )
        await service.process_next_turn(worker_id="test-worker")

        events = [
            frame
            async for frame in service.stream_events(conversation.id, accepted["turn_id"])
        ]
        event_text = "".join(events)

        self.assertIn("turn_failed", event_text)
        self.assertNotIn("turn_completed", event_text)
        persisted = await service.get_conversation(conversation.id)
        self.assertEqual(persisted.draft_plan, [])
        self.assertNotIn("supervisor_decision", persisted.metadata)
        self.assertEqual(persisted.metadata["last_turn"]["status"], "failed")
        self.assertEqual(persisted.metadata["last_turn"]["error_id"], "planner-error-1")
        event_payloads = [
            json.loads(line.removeprefix("data: "))
            for frame in events
            for line in frame.splitlines()
            if line.startswith("data: ")
        ]
        failure_event = next(event for event in event_payloads if event["type"] == "turn_failed")
        self.assertEqual(failure_event["payload"]["error_id"], "planner-error-1")


if __name__ == "__main__":
    unittest.main()
