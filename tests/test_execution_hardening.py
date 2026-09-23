import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.graph import supervisor_node, worker_node
from app.execution.state import Task
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.infrastructure.redis.event_publisher import InMemoryRunEventPublisher
from app.infrastructure.redis.run_queue import InMemoryRunCommandQueue
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService


class TestExecutionHardening(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        os.environ["TESTING"] = "true"

    async def test_unavailable_supervisor_model_is_explicit(self) -> None:
        with patch("app.execution.llm.get_llm", side_effect=ConnectionError("Ollama offline")):
            result = await supervisor_node(
                {
                    "messages": ["Research vector databases"],
                    "plan": [],
                    "mode": "conversation",
                    "metadata": {"use_llm": True},
                }
            )

        self.assertIn("chưa thể tạo phản hồi hợp lệ", result["messages"][0].lower())
        self.assertIn("llm_error", result["metadata"])
        self.assertEqual(result["plan"] if "plan" in result else None, None)

    async def test_worker_timeout_returns_failed_task(self) -> None:
        task = Task(
            id=1,
            node="slow_worker",
            status="running",
            description="Slow task",
            timeout_seconds=1,
        )
        async def slow_worker(state):
            del state
            await asyncio.sleep(2)

        with patch(
            "app.execution.graph._execute_worker_node",
            new=slow_worker,
        ):
            result = await worker_node({"current_task": task, "mode": "executing"})

        self.assertEqual(result["plan"][0].status, "failed")
        self.assertIn("timeout", result["plan"][0].error.lower())

    async def test_retry_creates_new_queued_attempt_and_resets_failed_tasks(self) -> None:
        repository = PostgresRunRepository()
        queue = InMemoryRunCommandQueue()
        service = RunService(
            run_repository=repository,
            workflow_repository=None,
            execution_port=AsyncMock(),
            command_queue=queue,
            event_publisher=InMemoryRunEventPublisher(),
        )
        source = RunDocument(
            run_id="failed-run-hardening",
            flow_id="flow-hardening",
            user_id="default_user",
            status="failed",
            mode="executing",
            plan=[
                Task(id=1, node="done", status="done", description="Done"),
                Task(id=2, node="failed", status="failed", description="Failed", error="network"),
            ],
            result_storage=[{"task_id": 1, "result": "kept", "status": "done"}],
        )
        await repository.save(source)

        retry = await service.retry_run(source.run_id, user_id="default_user")

        self.assertIsNotNone(retry)
        self.assertEqual(retry.status, "queued")
        self.assertEqual(retry.plan[0].status, "done")
        self.assertEqual(retry.plan[1].status, "pending")
        self.assertEqual(retry.metadata["retry_of"], source.run_id)
        command = await queue.dequeue(timeout=1)
        self.assertEqual(command.run_id, retry.run_id)


if __name__ == "__main__":
    unittest.main()
