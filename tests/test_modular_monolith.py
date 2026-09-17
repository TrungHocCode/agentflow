import json
import os
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.state import State, Task
from app.modules.catalog.domain import AgentDefinition, ToolDefinition
from app.modules.catalog.service import CatalogService
from app.modules.conversations.models import ConversationMessage, ConversationRecord
from app.modules.conversations.service import ConversationService
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService
from app.shared.events import ExecutionEvent
from app.infrastructure.redis.event_publisher import InMemoryRunEventPublisher
from app.infrastructure.redis.run_queue import InMemoryRunCommandQueue
from app.workers.run_worker import RunWorker
from app.modules.workflows.domain import WorkflowRecord
from app.modules.workflows.schemas import WorkflowCreateRequest
from app.modules.workflows.service import WorkflowService
from app.modules.workflows.validator import validate_workflow_definition
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
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
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


class FakeRunRepository:
    def __init__(self) -> None:
        self.documents: Dict[str, RunDocument] = {}
        self.events: Dict[str, List[ExecutionEvent]] = {}

    async def save(self, document: RunDocument) -> None:
        self.documents[document.run_id] = document

    async def get(self, run_id: str) -> RunDocument | None:
        return self.documents.get(run_id)

    async def list(self, flow_id: str | None = None, limit: int = 50) -> List[RunDocument]:
        values = list(self.documents.values())
        if flow_id:
            values = [document for document in values if document.flow_id == flow_id]
        return values[:limit]

    async def find_by_idempotency_key(self, idempotency_key: str) -> RunDocument | None:
        return next(
            (
                document
                for document in self.documents.values()
                if document.idempotency_key == idempotency_key
            ),
            None,
        )

    async def claim(self, run_id: str) -> RunDocument | None:
        document = self.documents.get(run_id)
        if document is None or document.status != "queued":
            return None
        document.status = "running"
        return document

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

    async def create_plan(self, run_id: str, initial_state: State) -> State:
        self.created_plan_for = run_id
        return {
            "mode": "conversation",
            "plan": [make_task(1)],
            "logs": ["plan created by fake execution adapter"],
            "result_storage": [],
        }

    async def continue_conversation(self, run_id: str, message: str) -> State:
        return {
            "mode": "conversation",
            "plan": [make_task(1)],
            "logs": [f"received: {message}"],
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

        await service.approve_run(document.run_id)
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
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
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
            idempotency_key="request-1",
        )
        self.assertIsNotNone(document)
        self.assertEqual(document.status, "queued")

        worker = RunWorker(service=service, command_queue=queue)
        self.assertTrue(await worker.process_next(timeout=1))
        completed = await service.get_run(document.run_id)
        self.assertEqual(completed.status, "completed")
        self.assertFalse(await worker.process_next(timeout=0))

        same_document = await service.create_workflow_run(
            workflow_id="workflow-1",
            idempotency_key="request-1",
        )
        self.assertEqual(same_document.run_id, document.run_id)

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


class FakeConversationRepository:
    def __init__(self) -> None:
        self.conversations: Dict[str, ConversationRecord] = {}
        self.messages: Dict[str, List[ConversationMessage]] = {}

    async def create(self, conversation: ConversationRecord) -> ConversationRecord:
        self.conversations[conversation.id] = conversation
        return conversation

    async def get(self, conversation_id: str, user_id: str) -> ConversationRecord | None:
        conversation = self.conversations.get(conversation_id)
        return conversation if conversation and conversation.user_id == user_id else None

    async def list(self, user_id: str, limit: int = 50) -> List[ConversationRecord]:
        return [c for c in self.conversations.values() if c.user_id == user_id][:limit]

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
        messages = await service.list_messages(conversation.id)
        self.assertEqual(messages[0].role, "user")

    async def test_async_message_publishes_replayable_progress_events(self) -> None:
        from app.infrastructure.redis.conversation_event_publisher import (
            InMemoryConversationEventPublisher,
        )

        repository = FakeConversationRepository()
        publisher = InMemoryConversationEventPublisher()
        service = ConversationService(
            repository=repository,
            execution_port=FakeExecutionPort(),
            event_publisher=publisher,
        )
        conversation = await service.create_conversation(title="Async research chat")
        accepted = await service.start_message(
            conversation_id=conversation.id,
            content="Research local LLMs",
        )

        self.assertEqual(accepted["status"], "accepted")
        events = []
        async for frame in service.stream_events(
            conversation.id,
            turn_id=accepted["turn_id"],
        ):
            events.append(frame)

        self.assertIn("planning_started", "".join(events))
        self.assertIn("workflow_draft_updated", "".join(events))
        self.assertIn("planning_completed", "".join(events))


if __name__ == "__main__":
    unittest.main()
