import json
import os
import sys
import unittest
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.state import State, Task
from app.modules.catalog.domain import AgentDefinition, ToolDefinition
from app.modules.catalog.service import CatalogService
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService
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

    async def save(self, document: RunDocument) -> None:
        self.documents[document.run_id] = document

    async def get(self, run_id: str) -> RunDocument | None:
        return self.documents.get(run_id)

    async def list(self, flow_id: str | None = None, limit: int = 50) -> List[RunDocument]:
        values = list(self.documents.values())
        if flow_id:
            values = [document for document in values if document.flow_id == flow_id]
        return values[:limit]


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


class TestWorkflowBoundaries(unittest.IsolatedAsyncioTestCase):
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
        events = [
            json.loads(event.removeprefix("data: ").strip())
            async for event in service.stream_run_events(document.run_id)
        ]

        self.assertTrue(events)
        self.assertTrue(all(event["event_id"] for event in events))
        self.assertTrue(all(event["run_id"] == document.run_id for event in events))
        self.assertEqual(events[-1]["type"], "completed")
        self.assertEqual(events[-1]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
